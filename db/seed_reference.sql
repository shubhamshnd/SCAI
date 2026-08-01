-- Reference seed data for the structured domain tables (design.md Section 3).
--
-- WHAT THIS IS: a development-grade starter set of well-known Indian producers, ports and
-- corridors so that supplier discovery, HSN/GST resolution and rail/air/sea transit benchmarks
-- return something real instead of nothing. Before this file, sku_master, suppliers,
-- logistics_nodes and transport_benchmarks were all empty and the agent could only ever answer
-- from RAG plus web search.
--
-- WHAT THIS IS NOT: a verified vendor master. Plant coordinates are approximate (town-level,
-- not gate-level), capability lists are indicative, and transit benchmarks are typical figures
-- rather than contracted schedules. Every row carries data_source = 'seed:reference' so the
-- agent can label these as reference data and the UI can distinguish them from anything you
-- later load from your own ERP. Qualify a supplier before contracting.
--
-- Safe to re-run: idempotent via unique indexes + ON CONFLICT DO UPDATE.
--
--   psql -d scai -f db/seed_reference.sql

BEGIN;

-- Provenance column so seeded rows are never mistaken for your own master data.
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS data_source TEXT NOT NULL DEFAULT 'unknown';

-- Natural keys, so a second run updates rather than duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS suppliers_name_city_key ON suppliers (name, location_city);
CREATE UNIQUE INDEX IF NOT EXISTS logistics_nodes_name_key ON logistics_nodes (name);
CREATE UNIQUE INDEX IF NOT EXISTS transport_benchmarks_route_mode_key
  ON transport_benchmarks (origin_node_id, destination_node_id, mode);

-- === Materials ===============================================================
-- HSN codes drive the GST rate in the landed-cost build-up, which is why the agent needs a
-- material row even when the user types a plain name rather than an internal code.

INSERT INTO sku_master (sku_code, material_name, material_category, hsn_code, unit_of_measure) VALUES
  ('CU-ROD-8MM',   'Copper wire rod 8mm',            'metals',    '7408',  'MT'),
  ('CU-CATH-999',  'Copper cathode 99.99%',          'metals',    '7403',  'MT'),
  ('CU-WIRE-ENAM', 'Enamelled copper winding wire',  'metals',    '8544',  'MT'),
  ('CU-CBL-LT',    'LT power cable copper conductor','metals',    '8544',  'KM'),
  ('AL-INGOT-P1020','Aluminium ingot P1020',         'metals',    '7601',  'MT'),
  ('AL-ROD-9MM',   'Aluminium wire rod 9.5mm',       'metals',    '7605',  'MT'),
  ('ZN-INGOT-SHG', 'Zinc ingot SHG 99.995%',         'metals',    '7901',  'MT'),
  ('STL-TMT-FE500','TMT steel bars Fe500D',          'steel',     '7214',  'MT'),
  ('STL-HRC-2MM',  'Hot rolled coil 2.0mm',          'steel',     '7208',  'MT'),
  ('STL-CRC-1MM',  'Cold rolled coil 1.0mm',         'steel',     '7209',  'MT'),
  ('STL-GI-COIL',  'Galvanised steel coil',          'steel',     '7210',  'MT'),
  ('STL-BILLET',   'Steel billet 150mm',             'steel',     '7207',  'MT'),
  ('CHM-CAUSTIC',  'Caustic soda lye 48%',           'chemicals', '2815',  'MT'),
  ('CHM-SODAASH',  'Soda ash dense',                 'chemicals', '2836',  'MT'),
  ('CHM-PHENOL',   'Phenol',                         'chemicals', '2907',  'MT'),
  ('CEM-OPC53',    'Ordinary Portland cement 53 grade','general', '2523',  'MT'),
  ('AGR-WHEAT',    'Wheat',                          'agri',      '1001',  'MT'),
  ('AGR-MAIZE',    'Maize',                          'agri',      '1005',  'MT')
ON CONFLICT (sku_code) DO UPDATE SET
  material_name = EXCLUDED.material_name,
  material_category = EXCLUDED.material_category,
  hsn_code = EXCLUDED.hsn_code,
  unit_of_measure = EXCLUDED.unit_of_measure;

-- === Suppliers ===============================================================
-- Coordinates are town-level approximations. materials_supplied is matched with ILIKE by
-- db.find_suppliers_near, so entries are worded to catch the obvious search terms.

INSERT INTO suppliers (name, location_city, location_state, lat, lon, materials_supplied, certifications, data_source) VALUES
  -- Copper — Maharashtra / west India first, which is what a Raigad plant actually cares about
  ('Hindustan Copper Ltd — Taloja Copper Project', 'Taloja',      'Maharashtra',     19.0800, 73.1000,
     ARRAY['copper wire rod','copper cathode','copper'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Finolex Cables — Pimpri Works',                'Pimpri',      'Maharashtra',     18.6280, 73.8000,
     ARRAY['copper wire','copper conductor','LT power cable','electrical cable'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('Finolex Cables — Urse Works',                  'Urse',        'Maharashtra',     18.7200, 73.6200,
     ARRAY['copper wire','enamelled copper winding wire','electrical cable'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('RR Kabel — Silvassa Works',                    'Silvassa',    'Dadra & Nagar Haveli', 20.2700, 73.0100,
     ARRAY['copper wire','electrical cable','copper conductor'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('KEI Industries — Chinchpada Works',            'Chinchpada',  'Dadra & Nagar Haveli', 20.2800, 73.0200,
     ARRAY['copper wire','LT power cable','copper conductor'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('Polycab India — Halol Works',                  'Halol',       'Gujarat',         22.5000, 73.4700,
     ARRAY['copper wire','electrical cable','copper conductor'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('Hindalco Industries — Birla Copper, Dahej',    'Dahej',       'Gujarat',         21.7000, 72.5800,
     ARRAY['copper cathode','copper wire rod','copper'], ARRAY['ISO 9001','ISO 14001'], 'seed:reference'),
  ('Hindustan Copper Ltd — Khetri Copper Complex', 'Khetri',      'Rajasthan',       28.0000, 75.7800,
     ARRAY['copper cathode','copper wire rod','copper'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Hindustan Copper Ltd — Indian Copper Complex', 'Ghatsila',    'Jharkhand',       22.5800, 86.4700,
     ARRAY['copper cathode','copper concentrate','copper'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Havells India — Alwar Works',                  'Alwar',       'Rajasthan',       27.5500, 76.6300,
     ARRAY['copper wire','electrical cable'], ARRAY['ISO 9001','BIS'], 'seed:reference'),

  -- Aluminium / zinc
  ('Hindalco Industries — Renukoot Works',         'Renukoot',    'Uttar Pradesh',   24.2000, 83.0300,
     ARRAY['aluminium ingot','aluminium wire rod','aluminium'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Vedanta (BALCO) — Korba Smelter',              'Korba',       'Chhattisgarh',    22.3500, 82.7000,
     ARRAY['aluminium ingot','aluminium wire rod','aluminium'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Hindustan Zinc — Chanderiya Smelter',          'Chittorgarh', 'Rajasthan',       24.9000, 74.6300,
     ARRAY['zinc ingot','zinc','lead'], ARRAY['ISO 9001'], 'seed:reference'),

  -- Steel
  ('JSW Steel — Dolvi Works',                      'Dolvi',       'Maharashtra',     18.6500, 73.0500,
     ARRAY['hot rolled coil','TMT steel bars','steel billet','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('JSW Steel — Vijayanagar Works',                'Ballari',     'Karnataka',       15.1700, 76.7200,
     ARRAY['hot rolled coil','cold rolled coil','galvanised steel coil','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('Tata Steel — Jamshedpur Works',                'Jamshedpur',  'Jharkhand',       22.8000, 86.2000,
     ARRAY['hot rolled coil','cold rolled coil','TMT steel bars','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('Tata Steel — Kalinganagar Works',              'Kalinganagar','Odisha',          20.9500, 86.0500,
     ARRAY['hot rolled coil','steel billet','steel'], ARRAY['ISO 9001'], 'seed:reference'),
  ('SAIL — Bhilai Steel Plant',                    'Bhilai',      'Chhattisgarh',    21.1900, 81.3800,
     ARRAY['TMT steel bars','steel billet','rails','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('SAIL — Rourkela Steel Plant',                  'Rourkela',    'Odisha',          22.2400, 84.8600,
     ARRAY['hot rolled coil','galvanised steel coil','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('SAIL — Bokaro Steel Plant',                    'Bokaro',      'Jharkhand',       23.6700, 86.1500,
     ARRAY['hot rolled coil','cold rolled coil','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('RINL — Visakhapatnam Steel Plant',             'Visakhapatnam','Andhra Pradesh', 17.6300, 83.2000,
     ARRAY['TMT steel bars','steel billet','wire rod','steel'], ARRAY['ISO 9001','BIS'], 'seed:reference'),
  ('AM/NS India — Hazira Works',                   'Hazira',      'Gujarat',         21.1200, 72.6500,
     ARRAY['hot rolled coil','cold rolled coil','galvanised steel coil','steel'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Jindal Steel & Power — Raigarh Works',         'Raigarh',     'Chhattisgarh',    21.9000, 83.4000,
     ARRAY['TMT steel bars','steel billet','rails','steel'], ARRAY['ISO 9001'], 'seed:reference'),

  -- Chemicals
  ('Deepak Nitrite — Nandesari Works',             'Nandesari',   'Gujarat',         22.4000, 73.1000,
     ARRAY['phenol','specialty chemicals'], ARRAY['ISO 9001','ISO 14001'], 'seed:reference'),
  ('Tata Chemicals — Mithapur Works',              'Mithapur',    'Gujarat',         22.4000, 69.0000,
     ARRAY['soda ash dense','caustic soda lye','salt'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Aarti Industries — Vapi Works',                'Vapi',        'Gujarat',         20.3700, 72.9000,
     ARRAY['specialty chemicals','phenol'], ARRAY['ISO 9001'], 'seed:reference'),
  ('Grasim Industries — Vilayat Chlor-Alkali',     'Vilayat',     'Gujarat',         21.8000, 72.8000,
     ARRAY['caustic soda lye','chlorine'], ARRAY['ISO 9001'], 'seed:reference')
ON CONFLICT (name, location_city) DO UPDATE SET
  location_state = EXCLUDED.location_state,
  lat = EXCLUDED.lat,
  lon = EXCLUDED.lon,
  materials_supplied = EXCLUDED.materials_supplied,
  certifications = EXCLUDED.certifications,
  data_source = EXCLUDED.data_source;

-- === Logistics nodes =========================================================

INSERT INTO logistics_nodes (node_type, name, lat, lon, state) VALUES
  ('port',          'JNPT Nhava Sheva',      18.9500, 72.9500, 'Maharashtra'),
  ('port',          'Mundra Port',           22.8400, 69.7200, 'Gujarat'),
  ('port',          'Deendayal Port Kandla', 23.0300, 70.2200, 'Gujarat'),
  ('port',          'Chennai Port',          13.1000, 80.3000, 'Tamil Nadu'),
  ('port',          'Visakhapatnam Port',    17.6900, 83.3000, 'Andhra Pradesh'),
  ('port',          'Kolkata Port',          22.5400, 88.3100, 'West Bengal'),
  ('port',          'Kochi Port',             9.9700, 76.2600, 'Kerala'),
  ('icd',           'ICD Tughlakabad Delhi', 28.5100, 77.2700, 'Delhi'),
  ('icd',           'ICD Whitefield Bengaluru', 12.9900, 77.7500, 'Karnataka'),
  ('icd',           'ICD Sanathnagar Hyderabad', 17.4600, 78.4400, 'Telangana'),
  ('rail_terminal', 'Tondiarpet Goods Terminal Chennai', 13.1300, 80.2800, 'Tamil Nadu'),
  ('rail_terminal', 'Kalamboli Goods Yard',  19.0300, 73.1000, 'Maharashtra'),
  ('warehouse',     'Bhiwandi Warehousing Hub', 19.3000, 73.0600, 'Maharashtra')
ON CONFLICT (name) DO UPDATE SET
  node_type = EXCLUDED.node_type, lat = EXCLUDED.lat, lon = EXCLUDED.lon, state = EXCLUDED.state;

-- === Corridor transit benchmarks =============================================
-- Typical door-to-door transit, not contracted schedules. Rail figures assume wagonload/rake
-- movement between goods sheds and exclude indent waiting, which the agent adds separately.

INSERT INTO transport_benchmarks (origin_node_id, destination_node_id, mode, distance_km, typical_transit_days, source)
SELECT o.id, d.id, v.mode, v.distance_km, v.transit_days, 'seed:reference — typical, verify'
FROM (VALUES
  ('Chennai Port',          'ICD Tughlakabad Delhi', 'road', 2180, 4.0),
  ('Chennai Port',          'ICD Tughlakabad Delhi', 'rail', 2185, 5.5),
  ('Tondiarpet Goods Terminal Chennai', 'ICD Tughlakabad Delhi', 'rail', 2190, 5.5),
  ('JNPT Nhava Sheva',      'ICD Tughlakabad Delhi', 'road', 1420, 3.0),
  ('JNPT Nhava Sheva',      'ICD Tughlakabad Delhi', 'rail', 1400, 4.0),
  ('Mundra Port',           'ICD Tughlakabad Delhi', 'road', 1150, 2.5),
  ('Mundra Port',           'ICD Tughlakabad Delhi', 'rail', 1130, 3.5),
  ('JNPT Nhava Sheva',      'ICD Whitefield Bengaluru', 'road', 980, 2.5),
  ('JNPT Nhava Sheva',      'ICD Whitefield Bengaluru', 'rail', 1000, 4.0),
  ('Chennai Port',          'ICD Whitefield Bengaluru', 'road', 350, 1.0),
  ('Visakhapatnam Port',    'ICD Tughlakabad Delhi', 'rail', 1800, 5.0),
  ('Kolkata Port',          'ICD Tughlakabad Delhi', 'rail', 1470, 4.5),
  ('JNPT Nhava Sheva',      'Kalamboli Goods Yard',  'road',   35, 0.5),
  ('Kalamboli Goods Yard',  'Bhiwandi Warehousing Hub', 'road', 55, 0.5)
) AS v(origin_name, dest_name, mode, distance_km, transit_days)
JOIN logistics_nodes o ON o.name = v.origin_name
JOIN logistics_nodes d ON d.name = v.dest_name
ON CONFLICT (origin_node_id, destination_node_id, mode) DO UPDATE SET
  distance_km = EXCLUDED.distance_km,
  typical_transit_days = EXCLUDED.typical_transit_days,
  source = EXCLUDED.source,
  updated_at = now();

COMMIT;

\echo 'Seeded reference data. Row counts:'
SELECT 'sku_master' AS table, count(*) FROM sku_master
UNION ALL SELECT 'suppliers', count(*) FROM suppliers
UNION ALL SELECT 'logistics_nodes', count(*) FROM logistics_nodes
UNION ALL SELECT 'transport_benchmarks', count(*) FROM transport_benchmarks;
