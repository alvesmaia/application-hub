# Armazenamento de arquivos

Declare `"storage"` em `requires` e use uma conexão pelo nome:

```python
from hub_sdk import storage

conn = storage.connection("arquivos")
```

- No `hub dev`, grava em `.hub/storage/` (`LocalConnection`, mesma API).
- No hub, o broker de armazenamento fala com S3 ou SharePoint; a credencial nunca chega ao app.
- Caminhos são normalizados (`clean_path`); `..` e caminhos absolutos são recusados.

Métodos da conexão: [Referência do SDK](/developers/sdk/hub-sdk-storage). Regras: Contrato §22
([Contrato](/developers/contrato)).
