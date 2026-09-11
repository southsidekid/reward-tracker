from aiogram.fsm.state import State, StatesGroup


class AddMeeting(StatesGroup):
    waiting_id = State()
    waiting_offers = State()
