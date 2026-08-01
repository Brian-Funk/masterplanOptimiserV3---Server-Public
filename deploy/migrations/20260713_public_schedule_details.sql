DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'published_general_schedule_items'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'published_general_schedule_items'
              AND column_name = 'location_note'
        ) AND NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'published_general_schedule_items'
              AND column_name = 'location_address'
        ) THEN
            ALTER TABLE published_general_schedule_items
                RENAME COLUMN location_note TO location_address;
        ELSIF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'published_general_schedule_items'
              AND column_name = 'location_address'
        ) THEN
            ALTER TABLE published_general_schedule_items
                ADD COLUMN location_address VARCHAR;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'published_general_schedule_items'
              AND column_name = 'responsible'
        ) THEN
            ALTER TABLE published_general_schedule_items
                ADD COLUMN responsible VARCHAR;
        END IF;
    END IF;
END
$migration$;
