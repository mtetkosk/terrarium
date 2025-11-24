"""Migration script to make ev_estimate nullable in predictions table"""

import sqlite3
import sys
from pathlib import Path

def migrate_database():
    """Make ev_estimate nullable in predictions table"""
    db_path = Path("data/db/terrarium.db")
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return False
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        print("Starting migration to make ev_estimate nullable...")
        
        # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
        # Step 1: Create new table with nullable ev_estimate
        cursor.execute("""
            CREATE TABLE predictions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                prediction_date DATE NOT NULL,
                model_type VARCHAR NOT NULL,
                predicted_spread FLOAT NOT NULL,
                predicted_total FLOAT,
                win_probability_team1 FLOAT NOT NULL,
                win_probability_team2 FLOAT NOT NULL,
                ev_estimate FLOAT,
                confidence_score FLOAT NOT NULL,
                mispricing_detected BOOLEAN DEFAULT 0,
                created_at DATETIME,
                FOREIGN KEY(game_id) REFERENCES games(id),
                UNIQUE(game_id, prediction_date)
            )
        """)
        
        # Step 2: Copy data from old table to new table
        print("Copying data to new table...")
        cursor.execute("""
            INSERT INTO predictions_new (
                id, game_id, prediction_date, model_type, predicted_spread, 
                predicted_total, win_probability_team1, win_probability_team2, 
                ev_estimate, confidence_score, mispricing_detected, created_at
            )
            SELECT 
                id, game_id, prediction_date, model_type, predicted_spread, 
                predicted_total, win_probability_team1, win_probability_team2, 
                ev_estimate, confidence_score, mispricing_detected, created_at
            FROM predictions
        """)
        
        # Step 3: Drop old table
        cursor.execute("DROP TABLE predictions")
        
        # Step 4: Rename new table
        cursor.execute("ALTER TABLE predictions_new RENAME TO predictions")
        
        conn.commit()
        print("Migration completed successfully!")
        print("Made ev_estimate nullable in predictions table")
        
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"Error during migration: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    success = migrate_database()
    sys.exit(0 if success else 1)

