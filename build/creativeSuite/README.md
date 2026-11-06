# Creative Suite — CFOS-owned lifecycle

The `CinterForgeOS` repository is the source authority for **all platform
recipes** and the full Setup → Build → Stage → Test lifecycle of Creative Suite
applications. The application key, platform (`mac`, `linux`, `win`) and version
identify immutable Built and Staged products. The OS itself retains its
specialized `./CFOS … cinterforge-os …` lifecycle.

```bash
./CFOS setup cfimage mac Release
./CFOS build cfimage mac Release
./CFOS stage cfimage mac Release
./CFOS test cfimage mac Release

./CFOS all cfimage linux Release
./CFOS build cfimage win Release --dry-run
```

`CinterForgeServer/Cinter` delegates application lifecycle calls to `CFOS`;
Server does not carry another copy of the application recipes. The Server
Python package is named `cf`, not `cinter`. The root executable is still
called `Cinter` for consistency with the public command interface.

- **Recipes/adapters:** `build/creativeSuite/apps/<app>/<platform>`
- **Shared algorithms:** `build/creativeSuite/shared` 
- **Catalog:** `build/creativeSuite/input/Creative Suite.csv`
- **Build products:** `../Builds/CreativeSuite/Built/<app>/<platform>`
- **Staged products:** sibling `Builds/CreativeSuite/Staged` and registered Steam
  `steamapps/common/CinterForge/Applications` directories
- **Final ZIPs:** `../Builds/CreativeSuite/Zipped`
- **Logs:** application-owned logs under `Builds`, never in source repositories

App Build/Stage/Test must use real platform recipes and installed-product
checks. A successful dry-run is path/dispatch validation, **not** evidence of
an actual compilation, installed environment, or GUI smoke test. Ordinary
incremental builds preserve valid artifacts and caches. Requirements and
build-toolchain changes must invalidate the relevant affected caches.

Server-specific data generation, the full Python service, web API and Python
virtual environment remain in `CinterForgeServer`; CinterForgeConverter owns
`cfconverter` builds; CinterForgeWebApp owns the integrated Hugo site and its
portable mini-server. CFOS orchestrates their validated release products.
