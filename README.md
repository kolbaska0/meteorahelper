# LB CLMM SDK

#### Quote Testing

```
cargo t -p commons --test '*'
```

#### SDK Testing

```
1. cd ts-client
2. anchor localnet -- --features localnet
3. pnpm run test
```

#### Build

### Build TS Client

## Install pnpm

``` ps1
Invoke-WebRequest https://get.pnpm.io/install.ps1 -UseBasicParsing | Invoke-Expression
```

## Install pkg

``` bash
npm install -g pkg
```

## Build Client

``` bash
cd ts-client
pnpm i
pnpm run start-server
pkg dist/src/server/index.js --targets node18-win-x64 --output server.exe
```

### Build Python Client

## Install requirements

``` bash
cd python-client\dlmm
python -m venv .env
.\.env\Scripts\Activate.ps1
python -m pip install -r req.txt
```

## Dist

``` bash
pyinstaller --onefile --windowed .\LiquiDMonAppUI.py
cp ..\..\ts-client\server.exe .\dist\server.exe
```
