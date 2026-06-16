# engine/state_machine.py

class StateMachine:
    def __init__(self):
        self.state = "IDLE"
        self.position_side = None

    def can_enter(self):
        return self.state == "IDLE"

    def enter(self, side):
        self.state = "LONG"
        self.position_side = side

    def exit(self):
        self.state = "IDLE"
        self.position_side = None