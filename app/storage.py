"""Storage abstraction layer for accessing Parquet files from various sources."""
import io
from typing import BinaryIO
from urllib.parse import urlparse
import pyarrow.parquet as pq


def open_uri(source_uri: str) -> pq.ParquetFile:
    parsed = urlparse(source_uri)
    scheme = parsed.scheme.lower()
    
    if scheme == "file" or not scheme:
        # Local file - direct access
        path = parsed.path if scheme == "file" else source_uri
        return pq.ParquetFile(path)
    
    elif scheme == "gs":
        # Google Cloud Storage - STREAMING ACCESS
        try:
            import gcsfs
        except ImportError:
            raise ImportError(
                "gcsfs is required for streaming gs:// URIs. "
                "Install with: pip install gcsfs\n"
                "Note: This provides efficient streaming access for large files."
            )
        
        # Create GCS filesystem interface
        # This enables streaming access - no download to memory
        fs = gcsfs.GCSFileSystem()
        
        # Open file via filesystem (streaming) - PyArrow will read only what it needs based on filters
        print(f"Opening GCS file with streaming access: {source_uri}")
        return pq.ParquetFile(fs.open(source_uri, 'rb'))
    
    elif scheme == "s3":
        # AWS S3 - STREAMING ACCESS
        try:
            import s3fs
        except ImportError:
            raise ImportError(
                "s3fs is required for streaming s3:// URIs. "
                "Install with: pip install s3fs\n"
                "Note: This provides efficient streaming access for large files."
            )
        
        # Create S3 filesystem interface - streaming access - no download to memory
        fs = s3fs.S3FileSystem()
        
        # Open file via filesystem (streaming) - PyArrow will read only what it needs based on filters
        print(f"Opening S3 file with streaming access: {source_uri}")
        return pq.ParquetFile(fs.open(source_uri, 'rb'))
    
    else:
        raise ValueError(f"Unsupported URI scheme: {scheme}. Supported: file://, gs://, s3://")
