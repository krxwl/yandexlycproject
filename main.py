import asyncio
import os
import traceback
import urllib.request
from shazamio import Shazam
import logging
from bs4 import BeautifulSoup
import requests
from peewee import *
import speech_recognition as sr
from pydub import AudioSegment
from io import BytesIO
from PIL import Image

from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InputFile

API_TOKEN_MAPS = '9df19ab1b86fe986e5b105391929b28c'
API_TOKEN = '6171983103:AAG3qLxvjS7DdLIPkNTkUlsOMbuz8u49pwg'
URL_YANDEX_MUSIC = "https://music.yandex.ru/chart"
API_GEOCODER_KEY = '40d1649f-0493-4b70-98ba-98533de7710b'
logging.basicConfig(level=logging.INFO)

# инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
con = SqliteDatabase('users.db')


class BaseModel(Model):
    class Meta:
        database = con


class User(BaseModel):
    id = TextField(column_name='id')
    name = TextField(column_name='name')
    city = TextField(column_name='city')

    class Meta:
        table_name = 'users'


class UserForm(StatesGroup):
    name = State()
    city = State()
    getting_started = State()


# кнопки для клавиатуры
weather_button = types.KeyboardButton('⛅️')
music_button = types.KeyboardButton('🎵')
alice_button = types.KeyboardButton('👩')
route_button = types.KeyboardButton('🗺')
start_button = types.KeyboardButton('/start')

keyboard_start = types.ReplyKeyboardMarkup(resize_keyboard=True).row(start_button)
keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True).row(weather_button, music_button,
                                                               alice_button, route_button)


def geocode(address):
    geocoder_request = f"http://geocode-maps.yandex.ru/1.x/?apikey={API_GEOCODER_KEY}" \
                       f"&geocode={address}&format=json"
    response = requests.get(geocoder_request)

    if response:
        json_response = response.json()
    else:
        raise RuntimeError(
            """Ошибка выполнения запроса:
            {request}
            Http статус: {status} ({reason})""".format(
                request=geocoder_request, status=response.status_code, reason=response.reason))
    features = json_response["response"]["GeoObjectCollection"]["featureMember"]
    return features[0]["GeoObject"] if features else None


def get_ll_span(address):
    toponym = geocode(address)
    if not toponym:
        return (None, None)
    toponym_coodrinates = toponym["Point"]["pos"]
    toponym_longitude, toponym_lattitude = toponym_coodrinates.split(" ")
    ll = ",".join([toponym_longitude, toponym_lattitude])
    envelope = toponym["boundedBy"]["Envelope"]
    l, b = envelope["lowerCorner"].split(" ")
    r, t = envelope["upperCorner"].split(" ")
    dx = abs(float(l) - float(r)) / 2.0
    dy = abs(float(t) - float(b)) / 2.0
    span = f"{dx},{dy}"
    return ll, span


@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message, state: FSMContext):
    """
    ответ на команду '/start'
    """
    try:
        in_database = User.get(User.id == message.from_user.id)
        await message.reply('Извините, Вы уже есть в базе данных, давайте приступим к работе',
                            reply_markup=keyboard)
    except Exception:
        global user
        user = User()
        await UserForm.name.set()
        await message.reply('Давайте знакомиться! Введите свое имя',
                            reply_markup=types.ReplyKeyboardRemove())


@dp.message_handler(state=UserForm.getting_started)
async def send_welcome(message: types.Message, state: FSMContext):
    await state.reset_state()
    await message.reply("Привет!\n\nМеня зовут yandexbot и я умею следующие вещи:\n\nВывести топ 5 "
                        "песен на сегодня из чарта Яндекс.Музыки\n\nБыстро связать тебя с "
                        "Алисой\n\nВывести погоду на ближайшую неделю из Яндекс.Погоды\n\nВывести "
                        "балл пробок на данный момент\n\nПостроить маршрут с помощью Яндекс.Карт",
                        reply_markup=keyboard)


@dp.message_handler(state=UserForm.name)
async def process_name(message: types.Message, state: FSMContext):
    global user
    async with state.proxy() as data:
        data['name'] = message.text
        user.name = message.text
    await UserForm.next()
    await message.reply('Из какого вы города?')


@dp.message_handler(state=UserForm.city)
async def process_age(message: types.Message, state: FSMContext):
    global user
    await state.update_data(age=message.text)
    user.city = message.text
    user.id = message.from_user.id
    User.create(id=user.id, name=user.name, city=user.city)
    await message.reply('Отлично! Можно приступать пользоваться ботом',
                        reply_markup=keyboard_start)
    await UserForm.next()


@dp.message_handler(Text(equals='⛅️'))
async def send_weather(message: types.Message):
    global API_TOKEN_MAPS
    city = User.get(User.id == message.from_user.id).city
    try:
        res = requests.get("http://api.openweathermap.org/data/2.5/find",
                           params={'q': city, 'type': 'like', 'units': 'metric', 'APPID':
                               API_TOKEN_MAPS})
        data = res.json()
        cities = ["{} ({})".format(d['name'], d['sys']['country'])
                  for d in data['list']]
        city_id = data['list'][0]['id']
    except Exception as e:
        print("Exception (find):", e)
        pass
    try:
        res = requests.get("http://api.openweathermap.org/data/2.5/weather",
                           params={'id': city_id, 'units': 'metric', 'lang': 'ru',
                                   'APPID': API_TOKEN_MAPS})
        data = res.json()
        await message.reply(f'⛅ Погода в городе {city.capitalize()} на сегодня:\n'
                            f'Описание: {data["weather"][0]["description"]}\n'
                            f'Температура: {int(data["main"]["temp"])} °C\n'
                            f'Ощущается как: {int(data["main"]["feels_like"])} °C\n'
                            f'Скорость ветра: {int(data["wind"]["speed"])} м/c\n',
                            reply_markup=keyboard)
    except Exception as e:
        print("Exception (find):", e)
        pass


@dp.message_handler(Text(equals='🎵'))
async def send_top_of_the_yandex_music(message: types.Message):
    page = requests.get(URL_YANDEX_MUSIC)
    soup = BeautifulSoup(page.text, 'html.parser')
    all_songs = soup.findAll('a', class_='d-track__title deco-link deco-link_stronger')
    names_of_the_songs = [song.text.strip() for song in all_songs]
    returned_message = 'ТОП-10 из чарта Яндекс.Музыки на сегодня\n\n'
    for num, name in zip(range(1, 11), names_of_the_songs):
        returned_message += f"{num}. {name}\n"
    await message.reply(returned_message)


@dp.message_handler(Text(equals='👩'))
async def send_welcome(message: types.Message):
    await message.reply(f'''связь с алисой''')


@dp.message_handler(commands=['get_traffic'])
async def send_welcome(message: types.Message):
    args = message.get_args()
    if args:
        try:
            ll, spn = get_ll_span(args)
            static_api_request = f"http://static-maps.yandex.ru/1.x/?ll={ll}&l=map,trf&spn={spn}"
            response = requests.get(static_api_request)
            image = Image.open(BytesIO(response.content))
            image.save('img.png')
            await bot.send_photo(photo=InputFile('img.png'), chat_id=message.from_user.id)
        except Exception:
            print(Exception.__class__.__name__)
            await message.reply("Извини, но я не смог найти этот адрес :(")
        if os.path.isfile('img.png'):
            os.remove('img.png')
    else:
        await message.reply('Введите сообщение в формате /get_traffic <город>')


@dp.message_handler(content_types=['audio', 'voice'])
async def send_welcome(message: types.Message):
    file_id = None  # скачивание файла
    if message.audio:
        file_id = message.audio.file_id
    elif message.voice:
        file_id = message.voice.file_id
    if message.audio or message.voice:
        file = await bot.get_file(file_id)
        file_path = file.file_path
        await bot.download_file(file_path, 'song.ogg')
        shazam = Shazam()
        out = await shazam.recognize_song('song.ogg')
        try:
            try:
                pathw = out['track']
                song_photo = open('song_photo.png', 'wb').write(
                    urllib.request.urlopen(pathw['images']['coverart']).read())
                await bot.send_photo(photo=InputFile('song_photo.png'),
                                     caption=f'''Это же {pathw['title']}! Исполнитель: {pathw['subtitle']}''',
                                     chat_id=message.from_user.id)
                os.remove('song_photo.png')
            except Exception:
                await message.reply(f'''Извините, не могу распознать песню''')
            AudioSegment.from_file('song.ogg').export('song.wav', format='wav')
            r = sr.Recognizer()
            with sr.AudioFile('song.wav') as source:
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language='ru-RU')
            await message.reply(f'Расшифровка сообщения: {text}')
        except Exception:
            traceback.print_exc()
            await message.reply(f'''Извините, не могу распознать текст''')
        if os.path.isfile('song.wav'):
            os.remove('song.wav')
        if os.path.isfile('song.ogg'):
            os.remove('song.ogg')


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
