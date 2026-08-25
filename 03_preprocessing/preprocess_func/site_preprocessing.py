import duckdb

from .download import resolve_source, cleanup

def preprocess_sites(parquet_file, username=None, password=None, base_dir = None):

    path, is_temp = resolve_source(parquet_file, username=username, password=password)

    query = """
    SELECT * FROM read_parquet(?)
    WHERE MimeType IN ('image/jpeg', 'image/png')
    """

    try:
        result = duckdb.execute(query, (path,)).df()
    finally:
        cleanup(path, is_temp)

    result["deviceID"] = result["Name"].str.split("_").str[2]
    result["picture_type"] = (
        result["Name"].str.split("_").str[3].str.split(".").str[0]
    )

    result["url"] = base_dir + "/" + result["Path"]

    result.to_csv("../output/image_mapping.csv", index=False)
