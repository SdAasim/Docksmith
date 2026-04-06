# Docksmith

A simplified Docker-like build and runtime system built from scratch in Python.

---

## Requirements

| Requirement | Detail |
|---|---|
| **OS** | Linux only (Ubuntu 20.04+ recommended) |
| **Python** | 3.8 or newer |
| **Privileges** | `sudo` required for `chroot` + namespace isolation |
| **Docker** | Needed once only for `setup_base_image.sh`; not needed after |

> **macOS / Windows users**: Use a Linux VM (WSL2 Ubuntu, VirtualBox, or any cloud VM).
> The `chroot(2)` and namespace syscalls used for isolation are Linux-only.

---

## Project Structure

```
docksmith/
├── docksmith/
│   ├── __init__.py
│   ├── __main__.py      # python -m docksmith
│   ├── cli.py           # CLI entry point & argument parsing
│   ├── parser.py        # Docksmithfile parser (6 instructions)
│   ├── store.py         # ~/.docksmith/ — images, layers, cache
│   ├── layers.py        # deterministic tar creation & extraction
│   ├── cache.py         # cache key computation
│   ├── builder.py       # build engine (FROM/COPY/RUN/WORKDIR/ENV/CMD)
│   ├── runtime.py       # container runtime (docksmith run)
│   ├── isolation.py     # chroot + Linux namespaces primitive
│   └── importer.py      # import Docker-saved tar into local store
├── sample_app/
│   ├── Docksmithfile    # uses all 6 instructions
│   └── run.sh           # sample app entrypoint
├── setup.py
├── setup_base_image.sh  # one-time base image import
└── demo.sh              # full 8-scenario demo
```

---

## Installation

```bash
# 1. Clone the repo and install
git clone <repo>
cd docksmith
pip install -e .
```

---

## One-Time Setup: Import a Base Image

Docksmith never downloads anything during build or run.
You must import base images once before building.

```bash
# Requires Docker for this step only
bash setup_base_image.sh
```

This will:
1. Pull `alpine:3.18` via Docker
2. Save it as a tar file
3. Import it into `~/.docksmith/`
4. Delete the tar file

After this, Docker is not needed again.

**If you don't have Docker**, you can get a minimal rootfs tar from any source and import it:

```bash
# Download a prebuilt Alpine rootfs (one-time, on a machine with internet)
wget https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/x86_64/alpine-minirootfs-3.18.0-x86_64.tar.gz

# Wrap it in Docker-format manually — OR use a helper:
python -c "
import tarfile, json, io, hashlib

# Read the rootfs tar
with open('alpine-minirootfs-3.18.0-x86_64.tar.gz','rb') as f:
    inner = f.read()

# Build a minimal Docker-format tar
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:') as outer:
    # layer
    linfo = tarfile.TarInfo('layer.tar')
    linfo.size = len(inner)
    outer.addfile(linfo, io.BytesIO(inner))
    # manifest.json
    mj = json.dumps([{'Config':'cfg.json','Layers':['layer.tar']}]).encode()
    mi = tarfile.TarInfo('manifest.json'); mi.size = len(mj)
    outer.addfile(mi, io.BytesIO(mj))
    # cfg.json
    cj = json.dumps({'config':{'Env':['PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'],'Cmd':['/bin/sh'],'WorkingDir':''}}).encode()
    ci = tarfile.TarInfo('cfg.json'); ci.size = len(cj)
    outer.addfile(ci, io.BytesIO(cj))

with open('alpine.tar','wb') as f:
    f.write(buf.getvalue())
print('Done: alpine.tar')
"

python -m docksmith import alpine.tar alpine:3.18
```

---

## CLI Reference

### `docksmith build`

```bash
sudo python -m docksmith build -t myapp:latest .
sudo python -m docksmith build -t myapp:latest --no-cache .
```

- Reads `Docksmithfile` from the given context directory (default: `.`)
- Prints step number, cache status `[CACHE HIT]` / `[CACHE MISS]`, and timing
- Writes image manifest to `~/.docksmith/images/`

### `docksmith images`

```bash
python -m docksmith images
```

Lists all images. Columns: NAME, TAG, ID (12-char digest), CREATED.

### `docksmith rmi`

```bash
python -m docksmith rmi myapp:latest
```

Removes the image manifest and all its layer files.

### `docksmith run`

```bash
sudo python -m docksmith run myapp:latest
sudo python -m docksmith run -e GREETING=Hi myapp:latest
sudo python -m docksmith run myapp:latest sh -c "echo hello"
```

- `-e KEY=VALUE` overrides image ENV (repeatable)
- Trailing arguments override the image CMD
- Process runs fully isolated; files written inside cannot appear on the host

### `docksmith import`

```bash
python -m docksmith import image.tar name:tag
```

One-time import of a Docker-saved tar into the local store.

---

## Docksmithfile Instructions

| Instruction | Example | Produces layer? |
|---|---|---|
| `FROM` | `FROM alpine:3.18` | No |
| `WORKDIR` | `WORKDIR /app` | No |
| `ENV` | `ENV KEY=value` | No |
| `COPY` | `COPY . /app/` | **Yes** |
| `RUN` | `RUN sh -c "echo hi"` | **Yes** |
| `CMD` | `CMD ["sh", "/app/run.sh"]` | No |

**Cache key** for each `COPY`/`RUN` is a SHA-256 of:
- Previous layer digest (or base image digest for first step)
- Full instruction text
- Current WORKDIR
- Sorted ENV state
- For COPY: sorted `(path, sha256)` of source files

---

## State Layout

```
~/.docksmith/
├── images/
│   └── myapp_latest.json     # image manifest
├── layers/
│   └── <sha256hex>           # raw tar file, one per layer
└── cache/
    └── <cache_key_hex>       # maps cache key → layer digest
```

---

## Isolation Mechanism

Both `RUN` (during build) and `docksmith run` use the **same** function: `run_isolated()` in `isolation.py`.

It uses:
- `chroot(2)` — restricts the process's view of the filesystem to the assembled rootfs
- `unshare(CLONE_NEWPID)` — new PID namespace (container gets PID 1)
- `unshare(CLONE_NEWUTS)` — new hostname namespace
- `unshare(CLONE_NEWNS)` — new mount namespace (for proc mount)

**Pass/fail demo**: write a file inside the container → it must not appear on the host.

---

## Running the Full Demo

```bash
# One-time setup
bash setup_base_image.sh

# Full 8-scenario demo
bash demo.sh
```

---

## Troubleshooting

**`chroot: Operation not permitted`**
→ Run with `sudo`. chroot requires root or `CAP_SYS_CHROOT`.

**`image 'alpine:3.18' not found`**
→ Run `setup_base_image.sh` first.

**Cache never hits on rebuild**
→ Check that file timestamps are being zeroed in tars (they are by default in this implementation).

**Command not found inside container**
→ The binary must exist inside the image's rootfs. Alpine has `/bin/sh` but not `bash` by default.