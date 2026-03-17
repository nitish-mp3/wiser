from __future__ import annotations

from protocol import Device


class FakeHub:
    def __init__(self) -> None:
        self.states = {"relay1": "OFF"}

    def discover(self):
        return [Device(id="relay1", type="switch", state="OFF", name="Relay 1")]

    def send_command(self, device_id: str, state: str) -> bool:
        self.states[device_id] = state
        return True

    def poll_states(self):
        return dict(self.states)


def test_driver_send_command_updates_state():
    from drivers.wiser_local import WiserLocalDriver

    hub = FakeHub()
    driver = WiserLocalDriver(hub)
    assert driver.send_command("relay1", "ON") is True
    assert driver.poll_state("relay1") == "ON"
