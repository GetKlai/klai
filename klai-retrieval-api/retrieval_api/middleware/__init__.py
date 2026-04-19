"""SPEC-SEC-010 middleware package for retrieval-api.

Currently exposes the :mod:`auth` module which provides the fail-closed
authentication + rate-limit middleware. Keep this package flat — additional
middleware (e.g. payload-size guards) should live in their own modules.
"""
