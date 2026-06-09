# STIX2 ID Generation Checker (vendored)

> Vendored verbatim from
> [`OpenCTI-Platform/connectors`](https://github.com/OpenCTI-Platform/connectors/tree/master/shared/pylint_plugins/check_stix_plugin)
> so we run the exact same `pylint` plugin upstream CI runs. Path
> preserved so a future upstream submission is a no-rename move.

Custom `pylint` checker that flags `stix2` `_DomainObject` and `Relationship`
constructor calls that don't pass an explicit `id=` keyword argument. The
warning is `W9101` (`no_generated_id_stix`). Missing deterministic IDs lead to
object duplication and ID explosion in OpenCTI.

## Usage

```bash
cd shared/pylint_plugins/check_stix_plugin
PYTHONPATH=. pylint <path_to_code> \
    --disable=all \
    --enable=no_generated_id_stix,no-value-for-parameter,unused-import \
    --load-plugins linter_stix_id_generator
```

`make lint` wires this up for our repo.