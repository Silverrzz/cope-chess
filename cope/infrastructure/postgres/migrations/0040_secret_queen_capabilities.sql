ALTER TABLE engine_variant_capabilities DROP CONSTRAINT IF EXISTS engine_variant_capabilities_variant_check;
ALTER TABLE engine_variant_capabilities ADD CONSTRAINT engine_variant_capabilities_variant_check
  CHECK (variant IN ('standard', 'dfrc', 'duck', 'duck960', 'tak', 'secret-queen'));
