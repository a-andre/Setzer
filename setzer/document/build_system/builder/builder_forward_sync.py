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

import _thread as thread
import base64
import subprocess

import setzer.document.build_system.builder.builder_build as builder_build
from setzer.app.service_locator import ServiceLocator


class BuilderForwardSync(builder_build.BuilderBuild):

    def __init__(self):
        builder_build.BuilderBuild.__init__(self)

        self.config_folder = ServiceLocator.get_config_folder()
        self.forward_synctex_regex = ServiceLocator.get_regex_object(r'\nOutput:.*\nPage:([0-9]+)\nx:.*\ny:.*\nh:((?:[0-9]|\.)+)\nv:((?:[0-9]|\.)+)\nW:((?:[0-9]|\.)+)\nH:((?:[0-9]|\.)+)\nbefore:.*\noffset:.*\nmiddle:.*\nafter:.*')

        self.process = None

    def run(self, query):
        tex_filename = query.tex_filename

        if not query.can_sync:
            query.forward_sync_result = None
            return

        synctex_folder = self.config_folder + '/' + base64.urlsafe_b64encode(str.encode(query.tex_filename)).decode()
        arguments = ['synctex', 'view', '-i']
        arguments.append(str(query.forward_sync_data['line']) + ':' + str(query.forward_sync_data['line_offset']) + ':' + query.forward_sync_data['filename'])
        arguments.append('-o')
        arguments.append(query.tex_filename[:-3] + 'pdf')
        arguments.append('-d')
        arguments.append(synctex_folder)
        try:
            self.process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_not_working', 'synctex missing')
            return

        self.process.wait()

        rectangles = list()
        if self.process != None:
            raw = self.process.communicate()[0].decode('utf-8')
            self.process = None

            for match in self.forward_synctex_regex.finditer(raw):
                rectangle = dict()
                rectangle['page'] = int(match.group(1))
                rectangle['h'] = float(match.group(2))
                rectangle['v'] = float(match.group(3))
                rectangle['width'] = float(match.group(4))
                rectangle['height'] = float(match.group(5))
                rectangles.append(rectangle)

        if len(rectangles) > 0:
            query.forward_sync_result = rectangles
        else:
            query.forward_sync_result = None

    def stop_running(self):
        if self.process != None:
            self.process.kill()
            self.process = None


