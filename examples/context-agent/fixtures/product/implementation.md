# Atlas implementation snapshot

This is a supplied implementation snapshot, not a live service query.
The enforced Standard limit is 120 requests per minute per workspace.
The enforced Enterprise limit is 600 requests per minute per workspace.
The rate limiter returns HTTP 429 and Retry-After when the limit is exceeded.
The fixture contains no policy for a Free plan or contractual uptime guarantee.
