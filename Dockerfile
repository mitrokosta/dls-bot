FROM python:3.9

RUN ["pip", "install", "python-telegram-bot", "pillow"]
RUN ["pip", "install", "torch==1.10.2+cpu", "torchvision==0.11.3+cpu", "-f", "https://download.pytorch.org/whl/cpu/torch_stable.html"]

WORKDIR /bot
COPY main.py main.py
COPY style_transfer.py style_transfer.py
COPY config.json config.json
COPY vgg_for_NST.pth vgg_for_NST.pth
COPY styles.jpg styles.jpg
COPY styles styles

ENTRYPOINT ["python"]
CMD ["main.py"]