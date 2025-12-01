import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_cors import CORS 
import psycopg2 
import psycopg2.extras 
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, JWTManager, get_jwt_identity

# --- 設定與初始化 ---
load_dotenv()

# 資料庫連線設定 (Render 會自動提供 DATABASE_URL)
DATABASE_URL = os.environ.get('DATABASE_URL') or os.getenv('POSTGRES_URL')

# JWT 密鑰 (Token 加密用)
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'default-secret-key-please-change')
# 管理員密鑰 (用來建立新帳號)
ADMIN_MASTER_KEY = os.getenv('ADMIN_MASTER_KEY', 'default-admin-key-please-change')

if not DATABASE_URL:
    print("⚠️ 警告: 未設定 DATABASE_URL")

app = Flask(__name__)
CORS(app) 

app.config["JWT_SECRET_KEY"] = JWT_SECRET_KEY
jwt = JWTManager(app)

# --- 資料庫連線函式 (PostgreSQL) ---
def get_db_connection():
    try:
        url = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require' if url.hostname != 'localhost' else 'prefer'
        )
        return conn
    except Exception as e:
        print(f"❌ 資料庫連線失敗: {e}")
        return None

# --- 核心路由 ---

# 1. 管理員註冊使用者 (隱藏接口，給 Postman 用)
@app.route('/api/admin/register', methods=['POST'])
def admin_register():
    # 檢查是否帶有管理員密鑰
    admin_key = request.headers.get('X-Admin-Key')
    if admin_key != ADMIN_MASTER_KEY:
        return jsonify({"msg": "Forbidden: 密鑰錯誤，你沒有權限建立帳號"}), 403

    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"msg": "帳號或密碼不能為空"}), 400

    conn = get_db_connection()
    if not conn: return jsonify({"msg": "DB Error"}), 500
    cursor = conn.cursor()
    
    hashed_pw = generate_password_hash(password)

    try:
        # PostgreSQL 語法: RETURNING user_id
        sql = "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING user_id"
        cursor.execute(sql, (username, hashed_pw))
        new_user_id = cursor.fetchone()[0]
        conn.commit()
        return jsonify({"msg": f"使用者 {username} 建立成功 (ID: {new_user_id})"}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        return jsonify({"msg": "帳號已存在"}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"msg": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# 2. 使用者登入 (一般人使用)
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    conn = get_db_connection()
    if not conn: return jsonify({"msg": "DB Error"}), 500
    # 使用 RealDictCursor 讓結果像字典
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            # 發放 Token，裡面藏著 user_id
            token = create_access_token(identity=user['user_id'])
            return jsonify({"access_token": token, "username": user['username']}), 200
        else:
            return jsonify({"msg": "帳號或密碼錯誤"}), 401
    finally:
        cursor.close()
        conn.close()

# 3. 查詢 & 新增片單 (需登入)
@app.route('/api/media', methods=['GET', 'POST'])
@jwt_required() 
def media_api():
    current_user_id = get_jwt_identity() # 從 Token 取得是誰
    conn = get_db_connection()
    if not conn: return jsonify({"msg": "DB Error"}), 500
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        if request.method == 'GET':
            q = request.args.get('q', '')
            # 只撈取「這個使用者」的資料
            sql = "SELECT * FROM media_items WHERE user_id = %s"
            params = [current_user_id]
            
            if q:
                sql += " AND title ILIKE %s" # ILIKE 不分大小寫
                params.append(f"%{q}%")
            
            sql += " ORDER BY added_date DESC"
            cursor.execute(sql, tuple(params))
            items = cursor.fetchall()
            # 轉換為列表回傳
            return jsonify({"status": "success", "data": [dict(row) for row in items]})

        elif request.method == 'POST':
            data = request.json
            
            # 防呆：檢查是否重複 (只檢查該使用者的)
            check_sql = "SELECT media_id FROM media_items WHERE title = %s AND user_id = %s"
            cursor.execute(check_sql, (data['title'], current_user_id))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": f"《{data['title']}》已經在你的清單裡囉！"}), 409

            sql = """
                INSERT INTO media_items (user_id, title, media_type, status, current_progress, rating, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING media_id
            """
            val = (
                current_user_id, data['title'], data['media_type'], data['status'],
                data.get('progress'), data.get('rating'), data.get('comment')
            )
            cursor.execute(sql, val)
            new_id = cursor.fetchone()['media_id']
            conn.commit()
            return jsonify({"status": "success", "message": "新增成功", "media_id": new_id}), 201

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# 4. 刪除 & 更新 (需登入)
@app.route('/api/media/<int:media_id>', methods=['DELETE', 'PUT'])
@jwt_required()
def item_api(media_id):
    current_user_id = get_jwt_identity()
    conn = get_db_connection()
    if not conn: return jsonify({"msg": "DB Error"}), 500
    cursor = conn.cursor()

    try:
        if request.method == 'DELETE':
            # 確保只能刪除自己的資料 (AND user_id = ...)
            sql = "DELETE FROM media_items WHERE media_id = %s AND user_id = %s"
            cursor.execute(sql, (media_id, current_user_id))
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"status": "error", "message": "刪除失敗 (找不到或無權限)"}), 404
            return jsonify({"status": "success", "message": "刪除成功"}), 200

        elif request.method == 'PUT':
            data = request.json
            sql = """
                UPDATE media_items SET title=%s, media_type=%s, status=%s, 
                current_progress=%s, rating=%s, comment=%s 
                WHERE media_id=%s AND user_id=%s
            """
            val = (
                data['title'], data['media_type'], data['status'],
                data.get('progress'), data.get('rating'), data.get('comment'),
                media_id, current_user_id
            )
            cursor.execute(sql, val)
            conn.commit()
            if cursor.rowcount == 0:
                return jsonify({"status": "error", "message": "更新失敗 (找不到或無權限)"}), 404
            return jsonify({"status": "success", "message": "更新成功"}), 200

    except Exception as e:
        if conn: conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)