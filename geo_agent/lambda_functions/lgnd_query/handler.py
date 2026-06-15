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
import numpy as np


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
        # Query period 1
        path1 = f"{MONTHLY_PATH}/model_version={DEFAULT_MODEL_VERSION}/collection={DEFAULT_COLLECTION}/chip_size={DEFAULT_CHIP_SIZE}/dims={DEFAULT_DIMS}/geohash={gh}/year={year1}/month={month1:02d}/*.parquet"
        rows1 = con.execute(f"SELECT cell_id, embedding FROM read_parquet('{path1}', hive_partitioning=true) {bbox_filter}").fetchall()

        # Query period 2 (with bbox for output)
        path2 = f"{MONTHLY_PATH}/model_version={DEFAULT_MODEL_VERSION}/collection={DEFAULT_COLLECTION}/chip_size={DEFAULT_CHIP_SIZE}/dims={DEFAULT_DIMS}/geohash={gh}/year={year2}/month={month2:02d}/*.parquet"
        rows2 = con.execute(f"SELECT cell_id, embedding, bbox FROM read_parquet('{path2}', hive_partitioning=true) {bbox_filter}").fetchall()

        con.close()

        # Index period 2 by cell_id
        d2_map = {row[0]: (row[1], row[2]) for row in rows2}

        # Compute cosine similarity for matched cells
        changed_cells = []
        matched = 0

        for row in rows1:
            cell_id = row[0]
            if cell_id not in d2_map:
                continue
            matched += 1

            emb1 = np.array(row[1], dtype=np.float32)
            emb2 = np.array(d2_map[cell_id][0], dtype=np.float32)
            cell_bbox = d2_map[cell_id][1]

            norm1 = np.linalg.norm(emb1)
            norm2 = np.linalg.norm(emb2)
            if norm1 == 0 or norm2 == 0:
                continue

            sim = float(np.dot(emb1, emb2) / (norm1 * norm2))

            # Skip likely cloud/snow/nodata artifacts (degenerate embeddings)
            if sim < ARTIFACT_SIM_FLOOR:
                continue

            change_score = float(np.clip((SIM_HIGH - sim) / SIM_RANGE, 0.0, 1.0))

            if change_score >= min_change_score:
                changed_cells.append({
                    "cell_id": cell_id,
                    "change_score": round(change_score, 4),
                    "similarity": round(sim, 4),
                    "bbox": cell_bbox,
                })

        return {
            "statusCode": 200,
            "changed_cells": changed_cells,
            "total_d1": len(rows1),
            "total_d2": len(rows2),
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
