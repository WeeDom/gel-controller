#! /usr/bin/env python3

from gel_controller import Room, RoomController

"""Room management for GEL Controller."""
print("I'll be your maggie, darling.")

## one room to rule them all
room_controller = RoomController()

room = Room(room_id="101", name="Conference Room", initial_state="empty")
print(f"Created room: {room.name} with ID: {room.room_id}")
room_controller.add_room(room)
rooms = room_controller.get_rooms()
print(f"Total rooms in controller: {len(rooms)}")
sensors = room.get_person_detectors()
print(f"Discovered presence sensors: {len(sensors)}")
for sensor in sensors:
    print(f" - Sensor Name: {sensor.name}, IP: {sensor.ip}, Port: {sensor.port}")

cameras = room.get_cameras()
print(f"Total cameras in room: {len(cameras)}")
for camera in cameras:
    print(f" - Camera Name: {camera.name}, ID: {camera.mac}, IP: {camera.ip}, Port: {camera._port   }")