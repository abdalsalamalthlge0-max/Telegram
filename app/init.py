import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from .config import Config


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'admin.login'




def create_app():
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config.from_object(Config)


# Ensure upload folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['IMAGE_FOLDER'], exist_ok=True)


db.init_app(app)
login_manager.init_app(app)


with app.app_context():
# استيراد النماذج وإنشاء قاعدة البيانات إن لم توجد
from . import models
db.create_all()


# تسجيل بلوبرنت لوحة الإدارة
from .admin import admin_bp
app.register_blueprint(admin_bp, url_prefix='/admin')


# استيراد وبدء البوت في Thread منفصل
from .bot import start_bot
start_bot(app)


return app
