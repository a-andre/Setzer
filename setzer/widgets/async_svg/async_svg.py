#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017, 2018 Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from gi.repository import GLib


class AsyncSvg(Gtk.Bin):

    def __init__(self, filename, width, height):
        Gtk.Bin.__init__(self)

        self.filename = filename

        self.set_size_request(width, height)
        GLib.idle_add(self.load_image)

    def load_image(self):
        self.image = Gtk.Image.new_from_file(self.filename)
        self.image.show_all()
        self.add(self.image)


