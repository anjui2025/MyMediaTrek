-- *******************************************
-- MyMediaTrek 專案：PostgreSQL 結構腳本
-- *******************************************

CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_items (
    media_id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    media_type VARCHAR(10) NOT NULL,
    status VARCHAR(15) NOT NULL,
    current_progress VARCHAR(50),
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    release_year INTEGER,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);