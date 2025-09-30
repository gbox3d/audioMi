# audioMi

the audio processing tools 

## setup

```bash
uv init
uv add soundcard==0.4.5 numpy scipy
```

## quick setup
```bash
uv sync --frozen
```


## build

```bash
pyinstaller --onefile --windowed --name="audioMi"  main.py
```