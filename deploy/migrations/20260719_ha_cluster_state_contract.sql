BEGIN;

-- A blank database is initially created from SQLAlchemy metadata. Earlier
-- metadata treated this singleton as a normal autoincrementing table, while
-- upgraded installations retained the stricter original migration contract.
-- Refuse unexpected data, then reconcile both creation paths without changing
-- a valid cluster identity or lease generation.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.ha_cluster_state
        WHERE id <> 1 OR generation < 1
    ) THEN
        RAISE EXCEPTION
            'ha_cluster_state contains values outside the singleton generation contract';
    END IF;
END
$$;

ALTER TABLE public.ha_cluster_state
    ALTER COLUMN id DROP DEFAULT,
    ALTER COLUMN maintenance SET DEFAULT FALSE,
    ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

DROP SEQUENCE IF EXISTS public.ha_cluster_state_id_seq;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_record
        WHERE constraint_record.conrelid = 'public.ha_cluster_state'::regclass
          AND constraint_record.contype = 'c'
          AND regexp_replace(
                pg_get_expr(constraint_record.conbin, constraint_record.conrelid),
                '[[:space:]]', '', 'g'
              ) IN ('(id=1)', 'id=1')
    ) THEN
        ALTER TABLE public.ha_cluster_state
            ADD CONSTRAINT ha_cluster_state_id_check CHECK (id = 1);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_record
        WHERE constraint_record.conrelid = 'public.ha_cluster_state'::regclass
          AND constraint_record.contype = 'c'
          AND regexp_replace(
                pg_get_expr(constraint_record.conbin, constraint_record.conrelid),
                '[[:space:]]', '', 'g'
              ) IN ('(generation>=1)', 'generation>=1')
    ) THEN
        ALTER TABLE public.ha_cluster_state
            ADD CONSTRAINT ha_cluster_state_generation_check
            CHECK (generation >= 1);
    END IF;
END
$$;

COMMIT;
