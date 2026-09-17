# Settlement layer derived from the Global Human Settlement Layer

`ghsl_r2023a_e2020_30ss_settled.bin` holds one bit per 30 arc-second pixel of the
world, set where either of these GHSL R2023A rasters records a positive value for
epoch 2020:

- GHS-BUILT-S, built-up surface:
  `GHS_BUILT_S_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip`, SHA-256
  `1bb109f506bd66605eee9f2d2dca21ed937e089f5adf5d1d388cbebe0614fba8`
- GHS-POP, population:
  `GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip`, SHA-256
  `579fb7477b33d9be61e9562b170ea108a670b85ef6fe23b61a22d17200929636`

Both were downloaded on 17 September 2026 from
https://human-settlement.emergency.copernicus.eu/download.php. The file is made by
`services/keys/tools/derive_settlement_mask.py`, and its SHA-256 is
`32a06c18a8249d20e004af74218e2757b2bbbd891e241b1f8dfc387346b17203`.

Source: European Commission, Joint Research Centre, Global Human Settlement Layer,
GHSL Data Package 2023. The GHSL is produced under a free and open data policy,
and its reuse is permitted with acknowledgement of the source, given here.

CASS reads the layer to keep only grid cells near anywhere a building stands or a
resident lives (`cass_keys.settlement`). It cannot see buildings GHSL did not
detect, such as isolated industrial sites far from any settlement.
