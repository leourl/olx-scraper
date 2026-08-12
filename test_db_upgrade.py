from app import create_app
from app.extensions import db
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

app = create_app()
with app.app_context():
    try:
        db.session.execute(text("SELECT * FROM non_existent_table"))
    except OperationalError as e:
        print("ERROR_CAUGHT:", str(e))
