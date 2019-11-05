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

from app.service_locator import ServiceLocator


class ShortcutsbarPresenter(object):
    ''' Mediator between workspace and view. '''
    
    def __init__(self, document, view):
        self.document = document
        self.view = view
        self.document.register_observer(self)
        
    '''
    *** notification handlers, get called by observed workspace
    '''

    def change_notification(self, change_code, notifying_object, parameter):

        if change_code == 'document_empty':
            document = parameter
            self.view.wizard_button.label_revealer.set_reveal_child(True)
            #self.view.wizard_button.get_child().get_style_context().add_class('suggested-action')

        if change_code == 'document_not_empty':
            document = parameter
            self.view.wizard_button.label_revealer.set_reveal_child(False)
            #self.view.wizard_button.get_child().get_style_context().remove_class('suggested-action')


