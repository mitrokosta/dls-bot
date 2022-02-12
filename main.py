import json
import os
from multiprocessing import Process
from queue import Queue
from threading import Thread
from uuid import uuid4
from pathlib import Path
from functools import partial

from telegram import Update, PhotoSize, Bot

from telegram.ext import Updater, CallbackContext, CommandHandler, MessageHandler, Filters

from style_transfer import TrainModel

config = {}
updater = None
dispatcher = None
sessions = {}
processing_queue = Queue()

help_str = """
Список команд:
/start - вывести приветственное сообщение
/help - вывести список команд
/transform - начать трансформацию или получить статус
/abort - отменить уже начатую трансформацию
/styles - вывести список предложенных стилей
"""


styles_str = """
Выбор фильтра:
/s1 /s2 /s3 /s4 /s5 /s6
"""


def do_style_transfer(user_id: int, content, style):
    content_path = f'photo.{user_id}.jpg'
    style_path = f'filter.{user_id}.jpg'
    out_path = f'out.{user_id}.jpg'

    content.get_file().download(custom_path=content_path)
    if isinstance(style, PhotoSize):
        style.get_file().download(custom_path=style_path)
    else:
        style_path = style
    
    model = TrainModel(content_path, style_path)
    model.run_style_transfer(out_path)

    with open(out_path, 'rb') as f:
        out = f.read()

    os.remove(content_path)
    if isinstance(style, PhotoSize):
        os.remove(style_path)
    os.remove(out_path)

    return out


def read_config():
    global config
    try:
        with open('config.json', 'r') as f:
            text = f.read()
            config = json.loads(text)
    except OSError:
        raise RuntimeError('Unable to load config file')


def start(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text=f'{update.effective_user.first_name}, привет! Команда /help выведет список доступных команд.')


def print_help(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text=help_str)


def abort(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id in sessions:
        if sessions[user_id]['state'] == 'processing':
            sessions[user_id]['proc'].kill()
        else:
            sessions.pop(user_id)
        context.bot.send_message(chat_id=update.effective_chat.id, text='Трансформация отменена.')
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text='Трансформации не было.')


def transform(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in sessions:
        context.bot.send_message(chat_id=update.effective_chat.id, text='Для трансформации мне понадобится исходная картинка. Пришли её, и мы начнём.')
    elif sessions[user_id]['state'] == 'content':
        context.bot.send_message(chat_id=update.effective_chat.id, text='Теперь мне понадобится фильтр (тоже картинка). Пришли его или выбери из предложенных (/styles).')
    elif sessions[user_id]['state'] == 'ready':
        sessions[user_id]['state'] = 'enqueued'
        request_id = uuid4()
        sessions[user_id]['uuid'] = request_id
        processing_queue.put((context.bot, update.effective_chat.id, user_id, request_id))
        context.bot.send_message(chat_id=update.effective_chat.id, text='Запрос помещён в очередь. Ты получишь уведомление, когда я начну его обрабатывать.')
    elif sessions[user_id]['state'] == 'enqueued':
        context.bot.send_message(chat_id=update.effective_chat.id, text='Запрос всё ещё в очереди. Отменить его можно с помощью /abort')
    elif sessions[user_id]['state'] == 'processing':
        context.bot.send_message(chat_id=update.effective_chat.id, text='Обработка в процессе. Завершить её принудительно можно с помощью /abort')


def transform_proc(bot: Bot, chat_id: int, user_id: int):
    bot.send_message(chat_id=chat_id, text='Запрос обрабатывается. Жди (примерно 5 минут).')
    result = do_style_transfer(user_id, sessions[user_id]['content'], sessions[user_id]['style'])
    bot.send_message(chat_id=chat_id, text='Готово!')
    bot.send_photo(chat_id=chat_id, photo=result)


def received_image(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in sessions:
        sessions[user_id] = {}
        sessions[user_id]['state'] = 'init'

    session = sessions[user_id]
    if session['state'] == 'init':
        session['content'] = update.message.photo[-1]
        session['state'] = 'content'
        context.bot.send_message(chat_id=update.effective_chat.id, text='Теперь мне понадобится фильтр (тоже картинка). Пришли его или выбери из предложенных (/styles).')
    elif session['state'] == 'content':
        session['state'] = 'ready'
        session['style'] = update.message.photo[-1]
        context.bot.send_message(chat_id=update.effective_chat.id, text='Принято! Теперь используй команду /transform для трансформации.')


def queue_thread():
    while True:
        bot, chat_id, user_id, request_id = processing_queue.get()
        if user_id not in sessions or 'uuid' not in sessions[user_id] or sessions[user_id]['uuid'] != request_id:
            continue

        sessions[user_id]['state'] = 'processing'
        sessions[user_id]['proc'] = Process(target=transform_proc, args=(bot, chat_id, user_id))
        sessions[user_id]['proc'].start()
        sessions[user_id]['proc'].join()
        sessions.pop(user_id)


def select_style(filename: str, update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in sessions:
        return

    session = sessions[user_id]

    if session['state'] == 'content':
        session['state'] = 'ready'
        session['style'] = filename
        context.bot.send_message(chat_id=update.effective_chat.id, text='Принято! Теперь используй команду /transform для трансформации.')


def print_styles(update: Update, context: CallbackContext):
    with open('styles.jpg', 'rb') as f:
        photo = f.read()
    context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo, caption=styles_str)


def register_command(name: str, method):
    handler = CommandHandler(name, method)
    dispatcher.add_handler(handler)


def register_message_handler(filters, method):
    handler = MessageHandler(filters, method)
    dispatcher.add_handler(handler)


def load_updater():
    global updater, dispatcher
    updater = Updater(token=config['token'], use_context=True)
    dispatcher = updater.dispatcher


def register_styles():
    for i in range(6):
        register_command(f's{i + 1}', partial(select_style, f'styles/{i + 1}.jpg'))


if __name__ == '__main__':
    read_config()
    load_updater()
    register_command('start', start)
    register_command('help', print_help)
    register_command('transform', transform)
    register_command('abort', abort)
    register_command('styles', print_styles)
    register_styles()
    register_message_handler(Filters.photo & (~Filters.command), received_image)

    t = Thread(target=queue_thread)
    t.start()

    updater.start_webhook(listen='0.0.0.0', port=os.environ.get('PORT'), webhook_url=config['webhook_url'])
    updater.idle()
