ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_driver_id INTEGER REFERENCES users(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_trip_id INTEGER REFERENCES orders(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_offer_outbox_id INTEGER;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_auto_excluded_driver_ids TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_route_fallback BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS parallel_auto_offers_disabled BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE orders SET parallel_route_fallback=TRUE WHERE last_decline_reason='route_parallel_fallback' AND parallel_route_fallback=FALSE;
CREATE INDEX IF NOT EXISTS ix_orders_parallel_offer_driver_id ON orders(parallel_offer_driver_id);
CREATE INDEX IF NOT EXISTS ix_orders_parallel_offer_trip_id ON orders(parallel_offer_trip_id);
