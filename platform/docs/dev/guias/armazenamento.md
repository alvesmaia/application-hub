# File storage

Declare `"storage"` in `requires` and use a connection by name:

```python
from hub_sdk import storage

conn = storage.connection("arquivos")
```

- In `hub dev`, it writes to `.hub/storage/` (`LocalConnection`, same API).
- In the hub, the storage broker talks to S3 or SharePoint; the credential never reaches the app.
- Paths are normalized (`clean_path`); `..` and absolute paths are refused.

Connection methods: [SDK reference](/developers/sdk/hub-sdk-storage). Rules: Contract §22
([Contract](/developers/contrato)).
