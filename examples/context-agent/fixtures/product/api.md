# Atlas API reference

The Atlas Standard plan allows 120 requests per minute per workspace.
The Atlas Enterprise plan allows 600 requests per minute per workspace.
Requests above the limit return HTTP 429 with a Retry-After header.
Clients should wait for Retry-After before retrying.
