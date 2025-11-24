"""Migration script to remove team1 and team2 columns from games table"""

import sqlite3
import sys
from pathlib import Path

def migrate_database():
    """Remove team1 and team2 columns from games table"""
    db_path = Path("data/db/terrarium.db")
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return False
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    try:
        # SQLite doesn't support DROP COLUMN directly, so we need to recreate the table
        print("Starting migration to remove team1 and team2 columns...")
        
        # Step 1: Create new table without team1 and team2
        cursor.execute("""
            CREATE TABLE games_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team1_id INTEGER NOT NULL,
                team2_id INTEGER NOT NULL,
                date DATE NOT NULL,
                venue VARCHAR,
                status VARCHAR(9),
                result JSON,
                created_at DATETIME,
                FOREIGN KEY(team1_id) REFERENCES teams(id),
                FOREIGN KEY(team2_id) REFERENCES teams(id)
            )
        """)
        
        # Step 2: Copy data from old table to new table (only games with valid team_ids)
        print("Copying data to new table...")
        cursor.execute("""
            INSERT INTO games_new (id, team1_id, team2_id, date, venue, status, result, created_at)
            SELECT id, team1_id, team2_id, date, venue, status, result, created_at
            FROM games
            WHERE team1_id IS NOT NULL AND team2_id IS NOT NULL
        """)
        
        deleted_count = cursor.execute("""
            SELECT COUNT(*) FROM games 
            WHERE team1_id IS NULL OR team2_id IS NULL
        """).fetchone()[0]
        
        if deleted_count > 0:
            print(f"Note: {deleted_count} games with missing team_ids were not migrated")
        
        # Step 3: Drop old table
        cursor.execute("DROP TABLE games")
        
        # Step 4: Rename new table
        cursor.execute("ALTER TABLE games_new RENAME TO games")
        
        # Step 5: Recreate indexes if any (you may need to add these)
        # cursor.execute("CREATE INDEX idx_games_date ON games(date)")
        # cursor.execute("CREATE INDEX idx_games_team1_id ON games(team1_id)")
        # cursor.execute("CREATE INDEX idx_games_team2_id ON games(team2_id)")
        
        conn.commit()
        print("Migration completed successfully!")
        print(f"Removed team1 and team2 columns from games table")
        
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"Error during migration: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    success = migrate_database()
    sys.exit(0 if success else 1)

