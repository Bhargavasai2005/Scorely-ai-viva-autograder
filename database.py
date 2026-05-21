import os
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv
import boto3

# Load the variables from the .env file
load_dotenv()

# Fetch credentials
DB_CONNECTION = os.getenv("DB_CONNECTION", "mysql")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_DATABASE = os.getenv("DB_DATABASE", "autograder_db")
DB_USERNAME = os.getenv("DB_USERNAME", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# --- SMART URL LOGIC ---
# If we are using SQLite (Laptop), the URL is simple.
# If we are using MySQL or PostgreSQL (AWS), we need the full username/password/host format.
if DB_CONNECTION == "sqlite":
    # Get the absolute path to your current folder
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Use 'exam_history.db' consistently
    db_path = os.path.join(current_dir, "exam_history.db")
    DB_URL = f"sqlite:///{db_path}"
elif DB_CONNECTION == "mysql":
    # MySQL connection URL with pymysql driver
    DB_URL = f"mysql+pymysql://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}?charset=utf8mb4"
else:
    # This is the professional AWS PostgreSQL format
    DB_URL = f"{DB_CONNECTION}://{DB_USERNAME}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}"

def init_db():
    try:
        # Create the engine with connection pooling
        engine = create_engine(
            DB_URL,
            poolclass=QueuePool,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        
        # Test the connection
        with engine.connect() as conn:
            pass
        
        # Friendly print message
        display_host = DB_HOST if DB_CONNECTION != "sqlite" else "Local File"
        print(f"[OK] Database connection initialized for '{DB_DATABASE}' using {DB_CONNECTION} ({display_host})")
        
        return engine
    except Exception as e:
        print(f"[ERROR] Database setup error: {e}")
        return None

# Run the connection setup when the server starts
db_engine = init_db()