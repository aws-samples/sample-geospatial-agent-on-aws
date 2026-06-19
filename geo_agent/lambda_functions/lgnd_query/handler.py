"""`lgnd-partition-query` Lambda: change detection for one geohash partition.

Queries a single geohash partition of the public LGND Clay v1.5 embedding
dataset for two monthly time periods, computes per-cell cosine similarity
between the matched embeddings, and returns only the cells whose change score
clears the threshold. This is the fan-out worker invoked once per geohash by
the country/state-wide region scan; returning raw embeddings would be too large
for a Lambda response, so the comparison happens here and only changed cells
come back.

The calibration constants (SIM_HIGH, SIM_LOW, ARTIFACT_SIM_FLOOR) must stay in
sync with the main application so partition results and in-app results agree.

Input event:
{
    "geohash": "9x",
    "year1": 2019, "month1": 7,
    "year2": 2024, "month2": 7,
    # Region bounding box (lon/lat edges) used to filter the partition.
    "bbox": {"west": -109.06, "south": 36.99, "east": -102.04, "north": 41.0},
    "min_change_score": 0.15
}

Output:
{
    # Per cell: change_score in [0,1], raw cosine similarity, and the cell's
    # bbox as a struct {xmin, ymin, xmax, ymax} (from the dataset).
    "changed_cells": [{"cell_id": "abc", "change_score": 0.85, "similarity": 0.78, "bbox": {...}}, ...],
    "total_d1": 65749,       # cells found in period 1 within the bbox
    "total_d2": 65200,       # cells found in period 2 within the bbox
    "matched": 65200,        # cells present in both periods (compared)
    "above_threshold": 1234, # cells returned after the artifact floor + threshold
    "geohash": "9x"
}
"""
import json
import duckdb


MONTHLY_PATH = "s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/monthly-aggregated"
DEFAULT_MODEL_VERSION = "v1.5"
DEFAULT_COLLECTION = "sentinel-2-l2a"
DEFAULT_CHIP_SIZE = "1280m"
DEFAULT_DIMS = "256"

# Change-score calibration (must match the main app). Cosine similarity is
# mapped linearly onto [0, 1]: at/above SIM_HIGH = no change (score 0),
# at/below SIM_LOW = full change (score 1), scaled over SIM_RANGE between them.
SIM_HIGH = 0.90
SIM_LOW = 0.50
SIM_RANGE = SIM_HIGH - SIM_LOW

# Artifact guard: similarity below this is almost always cloud/snow/nodata in
# one period's monthly aggregate (degenerate embedding), not real land change.
ARTIFACT_SIM_FLOOR = 0.30


def handler(event, context):
    gh = event["geohash"]
    year1 = event["year1"]
    month1 = event["month1"]
    year2 = event["year2"]
    month2 = event["month2"]
    bbox = event["bbox"]
    min_change_score = event.get("min_change_score", 0.15)

    west, south, east, north = bbox["west"], bbox["south"], bbox["east"], bbox["north"]

    con = duckdb.connect()
    con.execute("SET home_directory='/tmp';")
    con.execute("SET extension_directory='/var/task/duckdb_extensions';")
    con.execute("LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET s3_endpoint='s3.us-west-2.amazonaws.com';")
    con.execute("SET s3_url_style='path';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")
    con.execute("SET s3_session_token='';")

    bbox_filter = f"WHERE bbox.xmin <= {east} AND bbox.xmax >= {west} AND bbox.ymin <= {north} AND bbox.ymax >= {south}"

    try:
        base = (f"{MONTHLY_PATH}/model_version={DEFAULT_MODEL_VERSION}"
                f"/collection={DEFAULT_COLLECTION}/chip_size={DEFAULT_CHIP_SIZE}"
                f"/dims={DEFAULT_DIMS}/geohash={gh}")
        path1 = f"{base}/year={year1}/month={month1:02d}/*.parquet"
        path2 = f"{base}/year={year2}/month={month2:02d}/*.parquet"

        # Cell counts. Reads only the cell_id column (parquet column pruning), so
        # this stays cheap and never materializes embeddings.
        counts = con.execute(f"""
            WITH d1 AS (SELECT cell_id FROM read_parquet('{path1}', hive_partitioning=true) {bbox_filter}),
                 d2 AS (SELECT cell_id FROM read_parquet('{path2}', hive_partitioning=true) {bbox_filter})
            SELECT (SELECT count(*) FROM d1),
                   (SELECT count(*) FROM d2),
                   (SELECT count(*) FROM d1 JOIN d2 USING (cell_id))
        """).fetchone()
        total_d1, total_d2, matched = int(counts[0]), int(counts[1]), int(counts[2])

        # Change detection. Cosine similarity is computed INSIDE DuckDB via
        # list_cosine_similarity, so the 256-dim embeddings never cross into
        # Python -- only cells past the artifact floor + change threshold come
        # back. This keeps the function fast and bounds peak memory (the previous
        # per-cell numpy approach materialized every embedding and peaked near the
        # memory limit on dense partitions).
        rows = con.execute(f"""
            WITH d1 AS (
                SELECT cell_id, embedding AS e1
                FROM read_parquet('{path1}', hive_partitioning=true) {bbox_filter}
            ),
            d2 AS (
                SELECT cell_id, embedding AS e2, bbox
                FROM read_parquet('{path2}', hive_partitioning=true) {bbox_filter}
            ),
            sims AS (
                SELECT d1.cell_id AS cell_id,
                       list_cosine_similarity(d1.e1, d2.e2) AS sim,
                       d2.bbox AS bbox
                FROM d1 JOIN d2 USING (cell_id)
            )
            SELECT cell_id, sim, bbox,
                   greatest(0.0, least(1.0, ({SIM_HIGH} - sim) / {SIM_RANGE})) AS change_score
            FROM sims
            WHERE sim >= {ARTIFACT_SIM_FLOOR}
              AND greatest(0.0, least(1.0, ({SIM_HIGH} - sim) / {SIM_RANGE})) >= {min_change_score}
        """).fetchall()

        con.close()

        changed_cells = [
            {
                "cell_id": r[0],
                "change_score": round(float(r[3]), 4),
                "similarity": round(float(r[1]), 4),
                "bbox": r[2],
            }
            for r in rows
        ]

        return {
            "statusCode": 200,
            "changed_cells": changed_cells,
            "total_d1": total_d1,
            "total_d2": total_d2,
            "matched": matched,
            "above_threshold": len(changed_cells),
            "geohash": gh,
        }

    except Exception as e:
        try:
            con.close()
        except:
            pass
        return {
            "statusCode": 200,
            "changed_cells": [],
            "total_d1": 0,
            "total_d2": 0,
            "matched": 0,
            "above_threshold": 0,
            "geohash": gh,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }
