#!/bin/sh

sudo hciconfig hci0 up
sudo hciconfig hci0 class 0x7a020c
sudo hciconfig hci0 piscan
hciconfig hci0