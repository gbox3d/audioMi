# audioMi

the audio processing tools 

## setup

```bash
uv init
uv add soundcard==0.4.5 numpy==2.2.6 scipy==1.16.2
```

## quick setup
```bash
uv sync --frozen
```


## build

```bash
pyinstaller --onefile --windowed --name="audioMi"  main.py
pyinstaller --windowed --name="audioMi"  main.py
```