# Migrate a context layer to CTXD

Adapt an existing application's context ingestion and retrieval to CTXD while preserving its interface, source references, and a working path back to the original provider.

The `migrate-context-to-ctxd` skill inspects the application, checks compatibility, implements a reversible adapter, and compares representative tasks through both backends.

**Availability:** The skill instructions are available. A complete migration walkthrough is not yet provided.

## Supported migrations

The skill operates on one application, one corpus, and one retrieval boundary. Source documents must be available as tracker-compatible text files or an existing export. It checks query behavior, source coverage, citations, freshness, and reader access before submitting data or replacing retrieval.

If a required behavior cannot be preserved, the skill explains the mismatch and a compatible alternative. Semantic retrieval and live APIs can remain alongside CTXD where the application needs them. New connectors, media extraction, tenant infrastructure, and conversational memory require separate integration work. Tracking an export directory does not refresh its upstream source.

## Use the skill

On the development machine holding this checkout, open a Codex session in the repository as the account authorized to edit the target application. The skill is stored at `.agents/skills/migrate-context-to-ctxd/SKILL.md` and requires no global installation.

Provide the target application and representative tasks in a prompt such as:

> $migrate-context-to-ctxd Migrate the context layer in /path/to/application to CTXD. Its source documents are in /path/to/application/docs. Preserve its interface, citations, and original provider. Run the same representative tasks through both providers, then verify switching back.

Make source material and runtime credentials available through the target project's normal configuration. The skill inspects the existing provider and integration points. The target is the application identified in the prompt.

## Migration results

| Result | Contents |
|---|---|
| **Compatibility assessment** | Source inventory, required behavior, access boundaries, and an exact CTXD artifact and interface. |
| **Reversible application change** | One adapted retrieval path, a source-to-ctxfs mapping, preserved source identifiers, and a selectable original provider. |
| **Validation evidence** | Representative tasks through both providers, relevant update and failure checks, and observed latency and context usage. Unavailable checks remain explicitly unverified. |
| **Rollback instructions** | A demonstrated switch back to the original retrieval path, retaining the original sources and provider configuration. |

Setup uses application-owned state and checks that reruns do not duplicate registration or reset unrelated data. Publishing, production cutover, and deletion of the original store require separate authorization.

## Support-assistant example

The [CTXD support assistant](../../../examples/README.md) demonstrates the retrieval boundary: official GitHub documentation is published to CTXD, and the assistant retrieves evidence to produce cited support answers.

The example preserves canonical source paths and validates read hashes and citation ranges. Its tests exercise publication changes, removals, and failures. Applying the skill to a different application requires that application's own representative tasks and compatibility checks.

## Reference

- [Agent instructions](SKILL.md)
- [CTXD architecture and compatibility notes](../../../README.md#architecture-notes)
- [Support example and deployment architectures](../../../examples/README.md)
