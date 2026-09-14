from aiogram.fsm.state import State, StatesGroup


class AddMeeting(StatesGroup):
    waiting_id = State()
    waiting_offers = State()
    waiting_dup_id = State()


class EditMeeting(StatesGroup):
    waiting_new_id = State()
    editing_offers = State()