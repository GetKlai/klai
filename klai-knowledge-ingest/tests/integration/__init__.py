"""Integration tests for knowledge-ingest.

These tests require more infrastructure than unit tests (Postgres, Qdrant,
and/or a stubbed crawl4ai) and are gated behind ``RUN_INTEGRATION=1`` so
CI speed stays reasonable.
"""
