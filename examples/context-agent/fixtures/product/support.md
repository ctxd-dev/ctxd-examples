# Atlas support guidance

For Standard customers, explain the limit of 120 requests per minute per workspace.
For HTTP 429 responses, respect the Retry-After header.
Do not recommend immediate retries: they can extend the rate-limit incident.
Escalate discrepancies between pricing and the API reference to the product owner.
