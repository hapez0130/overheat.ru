import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    DATABASE = os.path.join(os.path.dirname(__file__), 'database.db')
    STEAM_API_KEY = os.environ.get('STEAM_API_KEY') or 'YOUR_STEAM_API_KEY_HERE'
    CS2_APP_ID = 730
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin123'
