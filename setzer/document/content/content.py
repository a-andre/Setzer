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
gi.require_version('GtkSource', '4')
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject
from gi.repository import GtkSource

import re
import time
import difflib
import math

import setzer.document.content.parser.parser_dummy as parser_dummy
import setzer.document.content.parser.parser_bibtex as parser_bibtex
import setzer.document.content.parser.parser_latex as parser_latex
from setzer.app.service_locator import ServiceLocator
from setzer.helpers.observable import Observable
import setzer.helpers.timer as timer


class Content(Observable):

    def __init__(self, language):
        Observable.__init__(self)

        self.settings = ServiceLocator.get_settings()

        self.source_buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View.new_with_buffer(self.source_buffer)
        self.source_language_manager = ServiceLocator.get_source_language_manager()
        self.source_style_scheme_manager = ServiceLocator.get_source_style_scheme_manager()

        if language == 'bibtex': self.source_language = self.source_language_manager.get_language('bibtex')
        else: self.source_language = self.source_language_manager.get_language('latex')

        self.source_buffer.set_language(self.source_language)
        self.update_syntax_scheme()

        self.symbols = dict()
        self.symbols['bibitems'] = set()
        self.symbols['labels'] = set()
        self.symbols['included_latex_files'] = set()
        self.symbols['bibliographies'] = set()
        self.symbols['packages'] = set()
        self.symbols['packages_detailed'] = dict()
        self.symbols['blocks'] = list()

        if language == 'bibtex': self.parser = parser_bibtex.ParserBibTeX(self)
        elif language == 'latex': self.parser = parser_latex.ParserLaTeX(self)
        else: self.parser = parser_dummy.ParserDummy(self)

        self.color_manager = ServiceLocator.get_color_manager()
        self.font_manager = ServiceLocator.get_font_manager()

        options = self.settings.get_source_buffer_options()
        self.tab_width = options['tab_width']
        self.spaces_instead_of_tabs = options['spaces_instead_of_tabs']

        self.mover_mark = self.source_buffer.create_mark('mover', self.source_buffer.get_start_iter(), True)

        self.insert_position = 0

        self.synctex_tag_count = 0
        self.synctex_highlight_tags = dict()

        self.indentation_update = None
        self.indentation_tags = dict()

        self.placeholder_tag = self.source_buffer.create_tag('placeholder')
        self.placeholder_tag.set_property('background', '#fce94f')
        self.placeholder_tag.set_property('foreground', '#000')

        self.source_buffer.connect('mark-set', self.on_mark_set)
        self.source_buffer.connect('mark-deleted', self.on_mark_deleted)
        self.source_buffer.connect('insert-text', self.on_insert_text)
        self.source_buffer.connect('delete-range', self.on_delete_range)
        self.source_buffer.connect('changed', self.on_buffer_changed)
        self.source_buffer.connect('modified-changed', self.on_modified_changed)
        self.undo_manager = self.source_buffer.get_undo_manager()
        self.undo_manager.connect('can-undo-changed', self.on_can_undo_changed)
        self.undo_manager.connect('can-redo-changed', self.on_can_redo_changed)

        self.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if (section, item) == ('preferences', 'tab_width'):
            self.tab_width = self.settings.get_value('preferences', 'tab_width')
        if (section, item) == ('preferences', 'spaces_instead_of_tabs'):
            self.spaces_instead_of_tabs = self.settings.get_value('preferences', 'spaces_instead_of_tabs')

        if (section, item) in [('preferences', 'syntax_scheme'), ('preferences', 'syntax_scheme_dark_mode')]:
            self.update_syntax_scheme()

    def on_insert_text(self, buffer, location_iter, text, text_length):
        self.parser.on_text_inserted(buffer, location_iter, text, text_length)
        self.indentation_update = {'line_start': location_iter.get_line(), 'text_length': text_length}
        self.add_change_code('text_inserted', (buffer, location_iter, text, text_length))

    def on_delete_range(self, buffer, start_iter, end_iter):
        self.parser.on_text_deleted(buffer, start_iter, end_iter)
        self.indentation_update = {'line_start': start_iter.get_line(), 'text_length': 0}
        self.add_change_code('text_deleted', (buffer, start_iter, end_iter))

    def on_modified_changed(self, buffer):
        self.add_change_code('modified_changed')

    def on_can_undo_changed(self, undo_manager):
        self.add_change_code('can_undo_changed', self.undo_manager.can_undo())

    def on_can_redo_changed(self, undo_manager):
        self.add_change_code('can_redo_changed', self.undo_manager.can_redo())

    def on_buffer_changed(self, buffer):
        self.update_indentation_tags()

        self.update_placeholder_selection()

        self.add_change_code('buffer_changed', buffer)

        if self.is_empty():
            self.add_change_code('document_not_empty')
        else:
            self.add_change_code('document_empty')

    def on_mark_set(self, buffer, insert, mark, user_data=None):
        if mark.get_name() == 'insert':
            self.update_placeholder_selection()
            self.add_change_code('insert_mark_set')
        self.update_selection_state()

    def on_mark_deleted(self, buffer, mark, user_data=None):
        if mark.get_name() == 'insert':
            self.add_change_code('insert_mark_deleted')
        self.update_selection_state()

    def initially_set_text(self, text):
        self.source_buffer.begin_not_undoable_action()
        self.source_buffer.set_text(text)
        self.source_buffer.end_not_undoable_action()
        self.source_buffer.set_modified(False)

    def update_selection_state(self):
        self.add_change_code('selection_might_have_changed', self.source_buffer.get_has_selection())

    def update_syntax_scheme(self):
        name = self.settings.get_value('preferences', 'syntax_scheme')
        self.source_style_scheme_light = self.source_style_scheme_manager.get_scheme(name)
        name = self.settings.get_value('preferences', 'syntax_scheme_dark_mode')
        self.source_style_scheme_dark = self.source_style_scheme_manager.get_scheme(name)
        self.set_use_dark_scheme(ServiceLocator.get_is_dark_mode())

    def set_use_dark_scheme(self, use_dark_scheme):
        if use_dark_scheme: self.source_buffer.set_style_scheme(self.source_style_scheme_dark)
        else: self.source_buffer.set_style_scheme(self.source_style_scheme_light)

    def get_style_scheme(self):
        return self.source_buffer.get_style_scheme()

    def get_can_undo(self):
        return self.undo_manager.can_undo()

    def get_can_redo(self):
        return self.undo_manager.can_redo()

    #@timer.timer
    def update_indentation_tags(self):
        if self.indentation_update != None:
            start_iter = self.source_buffer.get_iter_at_line(self.indentation_update['line_start'])
            end_iter = start_iter.copy()
            end_iter.forward_chars(self.indentation_update['text_length'])
            end_iter.forward_to_line_end()
            start_iter.set_line_offset(0)
            text = self.source_buffer.get_text(start_iter, end_iter, True)
            for count, line in enumerate(text.splitlines()):
                for tag in start_iter.get_tags():
                    self.source_buffer.remove_tag(tag, start_iter, end_iter)
                number_of_characters = len(line.replace('\t', ' ' * self.tab_width)) - len(line.lstrip())
                if number_of_characters > 0:
                    end_iter = start_iter.copy()
                    end_iter.forward_chars(1)
                    self.source_buffer.apply_tag(self.get_indentation_tag(number_of_characters), start_iter, end_iter)
                start_iter.forward_line()

            self.indentation_update = None

    def get_indentation_tag(self, number_of_characters):
        try:
            tag = self.indentation_tags[number_of_characters]
        except KeyError:
            tag = self.source_buffer.create_tag('indentation-' + str(number_of_characters))
            tag.set_property('indent', -1 * number_of_characters * self.font_manager.get_char_width(' '))
            self.indentation_tags[number_of_characters] = tag
        return tag

    def insert_before_document_end(self, text):
        end_iter = self.source_buffer.get_end_iter()
        result = end_iter.backward_search('\\end{document}', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
        if result != None:
            self.insert_text_at_iter(result[0], '''
''' + text + '''

''', False)
        else:
            self.insert_text_at_cursor(text)

    def insert_text(self, line_number, offset, text, indent_lines=True):
        insert_iter = self.source_buffer.get_iter_at_line_offset(line_number, offset)
        self.insert_text_at_iter(insert_iter, text, indent_lines)

    def insert_text_at_iter(self, insert_iter, text, indent_lines=True):
        self.source_buffer.place_cursor(insert_iter)
        self.insert_text_at_cursor(text, indent_lines)

    def insert_text_at_cursor(self, text, indent_lines=True, select_dot=True):
        self.source_buffer.begin_user_action()

        # replace tabs with spaces, if set in preferences
        if self.spaces_instead_of_tabs:
            number_of_spaces = self.tab_width
            text = text.replace('\t', ' ' * number_of_spaces)

        dotcount = text.count('•')
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        bounds = self.source_buffer.get_selection_bounds()
        selection = ''
        if dotcount == 1:
            bounds = self.source_buffer.get_selection_bounds()
            if len(bounds) > 0:
                selection = self.source_buffer.get_text(bounds[0], bounds[1], True)
                if len(selection) > 0:
                    text = text.replace('•', selection, 1)

        if indent_lines:
            line_iter = self.source_buffer.get_iter_at_line(insert_iter.get_line())
            ws_line = self.source_buffer.get_text(line_iter, insert_iter, False)
            lines = text.split('\n')
            ws_number = len(ws_line) - len(ws_line.lstrip())
            whitespace = ws_line[:ws_number]
            final_text = ''
            for no, line in enumerate(lines):
                if no != 0:
                    final_text += '\n' + whitespace
                final_text += line
        else:
            final_text = text

        self.source_buffer.delete_selection(False, False)
        self.source_buffer.insert_at_cursor(final_text)

        if select_dot:
            dotindex = final_text.find('•')
            if dotcount > 0:
                selection_len = len(selection) if dotcount == 1 else 0
                start = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
                start.backward_chars(abs(dotindex + selection_len - len(final_text)))
                self.source_buffer.place_cursor(start)
                end = start.copy()
                end.forward_char()
                self.source_buffer.select_range(start, end)

        self.source_buffer.end_user_action()

    def insert_template(self, template_start, template_end):
        self.source_buffer.begin_user_action()

        bounds = self.source_buffer.get_bounds()
        text = self.source_buffer.get_text(bounds[0], bounds[1], True)
        line_count_before_insert = self.source_buffer.get_line_count()

        # replace tabs with spaces, if set in preferences
        if self.settings.get_value('preferences', 'spaces_instead_of_tabs'):
            number_of_spaces = self.settings.get_value('preferences', 'tab_width')
            template_start = template_start.replace('\t', ' ' * number_of_spaces)
            template_end = template_end.replace('\t', ' ' * number_of_spaces)

        bounds = self.source_buffer.get_bounds()
        self.source_buffer.insert(bounds[0], template_start)
        bounds = self.source_buffer.get_bounds()
        self.source_buffer.insert(bounds[1], template_end)

        bounds = self.source_buffer.get_bounds()
        bounds[0].forward_chars(len(template_start))
        self.source_buffer.place_cursor(bounds[0])

        self.source_buffer.end_user_action()
        self.source_buffer.begin_user_action()

        if len(text.strip()) > 0:
            note = _('''% NOTE: The content of your document has been commented out
% by the wizard. Just do a CTRL+Z (undo) to put it back in
% or remove the "%" before each line you want to keep.
% You can remove this note as well.
% 
''')
            note_len = len(note)
            note_number_of_lines = note.count('\n')
            offset = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()
            self.source_buffer.insert(self.source_buffer.get_iter_at_line(offset), note)
            for line_number in range(offset + note_number_of_lines, line_count_before_insert + offset + note_number_of_lines):
                self.source_buffer.insert(self.source_buffer.get_iter_at_line(line_number), '% ')
            insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
            insert_iter.backward_chars(note_len + 2)
            self.source_buffer.place_cursor(insert_iter)

        self.source_buffer.end_user_action()

    def replace_range_by_offset_and_length(self, offset, length, text, indent_lines=True, select_dot=True):
        start_iter = self.source_buffer.get_iter_at_offset(offset)
        end_iter = self.source_buffer.get_iter_at_offset(offset + length)
        self.replace_range(start_iter, end_iter, text, indent_lines, select_dot)

    def replace_range(self, start_iter, end_iter, text, indent_lines=True, select_dot=True):
        self.source_buffer.begin_user_action()
        self.replace_range_no_user_action(start_iter, end_iter, text, indent_lines, select_dot)
        self.source_buffer.end_user_action()

    def replace_range_no_user_action(self, start_iter, end_iter, text, indent_lines=True, select_dot=True):
        if indent_lines:
            line_iter = self.source_buffer.get_iter_at_line(start_iter.get_line())
            ws_line = self.source_buffer.get_text(line_iter, start_iter, False)
            lines = text.split('\n')
            ws_number = len(ws_line) - len(ws_line.lstrip())
            whitespace = ws_line[:ws_number]
            final_text = ''
            for no, line in enumerate(lines):
                if no != 0:
                    final_text += '\n' + whitespace
                final_text += line
        else:
            final_text = text

        self.source_buffer.delete(start_iter, end_iter)
        self.source_buffer.insert(start_iter, final_text)

        if select_dot:
            dotindex = final_text.find('•')
            if dotindex > -1:
                start_iter.backward_chars(abs(dotindex - len(final_text)))
                bound = start_iter.copy()
                bound.forward_chars(1)
                self.source_buffer.select_range(start_iter, bound)

    def insert_before_after(self, before, after):
        bounds = self.source_buffer.get_selection_bounds()

        if len(bounds) > 1:
            text = before + self.source_buffer.get_text(*bounds, 0) + after
            self.replace_range(bounds[0], bounds[1], text)
        else:
            text = before + '•' + after
            self.insert_text_at_cursor(text)

    def comment_uncomment(self):
        self.source_buffer.begin_user_action()

        bounds = self.source_buffer.get_selection_bounds()

        if len(bounds) > 1:
            end = (bounds[1].get_line() + 1) if (bounds[1].get_line_index() > 0) else bounds[1].get_line()
            line_numbers = list(range(bounds[0].get_line(), end))
        else:
            line_numbers = [self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()]

        do_comment = False
        for line_number in line_numbers:
            line = self.get_line(line_number)
            if not line.lstrip().startswith('%'):
                do_comment = True

        if do_comment:
            for line_number in line_numbers:
                self.source_buffer.insert(self.source_buffer.get_iter_at_line(line_number), '%')
        else:
            for line_number in line_numbers:
                line = self.source_buffer.get_line(line_number)
                offset = len(line) - len(line.lstrip())
                start = self.source_buffer.get_iter_at_line(line_number)
                start.forward_chars(offset)
                end = start.copy()
                end.forward_char()
                self.source_buffer.delete(start, end)

        self.source_buffer.end_user_action()

    def add_backslash_with_space(self):
        self.source_buffer.insert_at_cursor('\\ ')
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        insert_iter.backward_char()
        self.source_buffer.place_cursor(insert_iter)

    def autoadd_latex_brackets(self, char):
        if self.get_char_before_cursor() == '\\':
            add_char = '\\'
        else:
            add_char = ''
        if char == '[':
            self.source_buffer.begin_user_action()
            self.source_buffer.delete_selection(True, True)
            self.source_buffer.insert_at_cursor('[' + add_char + ']')
            self.source_buffer.end_user_action()
        if char == '{':
            self.source_buffer.begin_user_action()
            self.source_buffer.delete_selection(True, True)
            self.source_buffer.insert_at_cursor('{' + add_char + '}')
            self.source_buffer.end_user_action()
        if char == '(':
            self.source_buffer.begin_user_action()
            self.source_buffer.delete_selection(True, True)
            self.source_buffer.insert_at_cursor('(' + add_char + ')')
            self.source_buffer.end_user_action()
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        insert_iter.backward_char()
        if add_char == '\\':
            insert_iter.backward_char()
        self.source_buffer.place_cursor(insert_iter)

    def get_char_at_cursor(self):
        start_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        end_iter = start_iter.copy()
        end_iter.forward_char()
        return self.source_buffer.get_text(start_iter, end_iter, False)

    def get_char_before_cursor(self):
        start_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        end_iter = start_iter.copy()
        end_iter.backward_char()
        return self.source_buffer.get_text(start_iter, end_iter, False)

    def get_latex_command_at_cursor(self):
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        limit_iter = insert_iter.copy()
        limit_iter.backward_chars(50)
        word_start_iter = insert_iter.copy()
        result = word_start_iter.backward_search('\\', Gtk.TextSearchFlags.TEXT_ONLY, limit_iter)
        if result != None:
            word_start_iter = result[0]
        word = word_start_iter.get_slice(insert_iter)
        return word

    def get_latex_command_at_cursor_offset(self):
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        limit_iter = insert_iter.copy()
        limit_iter.backward_chars(50)
        word_start_iter = insert_iter.copy()
        result = word_start_iter.backward_search('\\', Gtk.TextSearchFlags.TEXT_ONLY, limit_iter)
        if result != None:
            word_start_iter = result[0]
            return word_start_iter.get_offset()
        return None

    def replace_latex_command_at_cursor(self, text, dotlabels, is_full_command=False):
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        current_word = self.get_latex_command_at_cursor()
        start_iter = insert_iter.copy()
        start_iter.backward_chars(len(current_word))

        if is_full_command and text.startswith('\\begin'):
            end_command = text.replace('\\begin', '\\end')
            end_command_bracket_position = end_command.find('}')
            if end_command_bracket_position:
                end_command = end_command[:end_command_bracket_position + 1]
            text += '\n\t•\n' + end_command
            if self.spaces_instead_of_tabs:
                number_of_spaces = self.tab_width
                text = text.replace('\t', ' ' * number_of_spaces)
            dotlabels += 'content###'
            if end_command.find('•') >= 0:
                dotlabels += 'environment###'

            line_iter = self.source_buffer.get_iter_at_line(start_iter.get_line())
            ws_line = self.source_buffer.get_text(line_iter, start_iter, False)
            lines = text.split('\n')
            ws_number = len(ws_line) - len(ws_line.lstrip())
            whitespace = ws_line[:ws_number]
            text = ''
            for no, line in enumerate(lines):
                if no != 0:
                    text += '\n' + whitespace
                text += line

        parts = text.split('•')
        if len(parts) == 1:
            orig_text = self.source_buffer.get_text(start_iter, insert_iter, False)
            if parts[0].startswith(orig_text):
                self.source_buffer.insert_at_cursor(parts[0][len(orig_text):])
            else:
                self.replace_range(start_iter, insert_iter, parts[0], indent_lines=True, select_dot=True)
        else:
            self.source_buffer.begin_user_action()

            self.source_buffer.delete(start_iter, insert_iter)
            insert_offset = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_offset()
            count = len(parts)
            select_dot_offset = -1
            for part in parts:
                insert_iter = self.source_buffer.get_iter_at_offset(insert_offset)
                insert_offset += len(part)
                self.source_buffer.insert(insert_iter, part)
                if count > 1:
                    insert_iter = self.source_buffer.get_iter_at_offset(insert_offset)
                    self.source_buffer.insert_with_tags(insert_iter, '•', self.placeholder_tag)
                    if select_dot_offset == -1:
                        select_dot_offset = insert_offset
                    insert_offset += 1
                count -= 1
            select_dot_iter = self.source_buffer.get_iter_at_offset(select_dot_offset)
            bound = select_dot_iter.copy()
            bound.forward_chars(1)
            self.source_buffer.select_range(select_dot_iter, bound)

            self.source_buffer.end_user_action()

    def get_line_at_cursor(self):
        return self.get_line(self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line())

    def get_line(self, line_number):
        start = self.source_buffer.get_iter_at_line(line_number)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return self.source_buffer.get_slice(start, end, False)

    def get_all_text(self):
        return self.source_buffer.get_text(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter(), True)

    def get_text_after_offset(self, offset):
        return self.source_buffer.get_text(self.source_buffer.get_iter_at_offset(offset), self.source_buffer.get_end_iter(), True)

    def get_selected_text(self):
        bounds = self.source_buffer.get_selection_bounds()
        if len(bounds) == 2:
            return self.source_buffer.get_text(bounds[0], bounds[1], True)
        else:
            return None

    def get_current_line_number(self):
        return self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()

    def is_empty(self):
        return self.source_buffer.get_end_iter().get_offset() > 0

    def update_placeholder_selection(self):
        if self.get_cursor_offset() != self.insert_position:
            if not self.source_buffer.get_selection_bounds():
                start_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
                prev_iter = start_iter.copy()
                prev_iter.backward_char()
                if start_iter.has_tag(self.placeholder_tag):
                    while start_iter.has_tag(self.placeholder_tag):
                        start_iter.backward_char()
                    if not start_iter.has_tag(self.placeholder_tag):
                        start_iter.forward_char()
                    end_iter = start_iter.copy()

                    tag_length = 0
                    while end_iter.has_tag(self.placeholder_tag):
                        tag_length += 1
                        end_iter.forward_char()

                    moved_backward_from_end = (self.insert_position == self.get_cursor_offset() + tag_length)
                    if not moved_backward_from_end:
                        self.source_buffer.select_range(start_iter, end_iter)
                elif prev_iter.has_tag(self.placeholder_tag):
                    while prev_iter.has_tag(self.placeholder_tag):
                        prev_iter.backward_char()
                    if not prev_iter.has_tag(self.placeholder_tag):
                        prev_iter.forward_char()
                    end_iter = prev_iter.copy()

                    tag_length = 0
                    while end_iter.has_tag(self.placeholder_tag):
                        tag_length += 1
                        end_iter.forward_char()

                    moved_forward_from_start = (self.insert_position == self.get_cursor_offset() - tag_length)
                    if not moved_forward_from_start:
                        self.source_buffer.select_range(prev_iter, end_iter)

            self.insert_position = self.get_cursor_offset()

    def set_synctex_position(self, position):
        start = self.source_buffer.get_iter_at_line(position['line'])
        end = start.copy()
        if not start.ends_line():
            end.forward_to_line_end()
        text = self.source_buffer.get_text(start, end, False)

        matches = self.get_synctex_word_bounds(text, position['word'], position['context'])
        if matches != None:
            for word_bounds in matches:
                end = start.copy()
                new_start = start.copy()
                new_start.forward_chars(word_bounds[0])
                end.forward_chars(word_bounds[1])
                self.add_synctex_tag(new_start, end)
        else:
            ws_number = len(text) - len(text.lstrip())
            start.forward_chars(ws_number)
            self.add_synctex_tag(start, end)

    def add_synctex_tag(self, start_iter, end_iter):
        self.source_buffer.place_cursor(start_iter)
        self.synctex_tag_count += 1
        self.source_buffer.create_tag('synctex_highlight-' + str(self.synctex_tag_count), background_rgba=self.color_manager.get_rgba(0.976, 0.941, 0.420, 0.6), background_full_height=True)
        tag = self.source_buffer.get_tag_table().lookup('synctex_highlight-' + str(self.synctex_tag_count))
        self.source_buffer.apply_tag(tag, start_iter, end_iter)
        if not self.synctex_highlight_tags:
            GObject.timeout_add(15, self.remove_or_color_synctex_tags)
        self.synctex_highlight_tags[self.synctex_tag_count] = {'tag': tag, 'time': time.time()}

    def get_synctex_word_bounds(self, text, word, context):
        if not word: return None
        word = word.split(' ')
        if len(word) > 2:
            word = word[:2]
        word = ' '.join(word)
        regex_pattern = re.escape(word)

        for c in regex_pattern:
            if ord(c) > 127:
                regex_pattern = regex_pattern.replace(c, '(?:\w)')

        matches = list()
        top_score = 0.1
        regex = ServiceLocator.get_regex_object(r'(\W{0,1})' + regex_pattern.replace('\x1b', r'(?:\w{2,3})').replace('\x1c', r'(?:\w{2})').replace('\x1d', r'(?:\w{2,3})').replace('\-', r'(?:-{0,1})') + r'(\W{0,1})')
        for match in regex.finditer(text):
            offset1 = context.find(word)
            offset2 = len(context) - offset1 - len(word)
            match_text = text[max(match.start() - max(offset1, 0), 0):min(match.end() + max(offset2, 0), len(text))]
            score = difflib.SequenceMatcher(None, match_text, context).ratio()
            if bool(match.group(1)) or bool(match.group(2)):
                if score > top_score + 0.1:
                    top_score = score
                    matches = [[match.start() + len(match.group(1)), match.end() - len(match.group(2))]]
                elif score > top_score - 0.1:
                    matches.append([match.start() + len(match.group(1)), match.end() - len(match.group(2))])
        if len(matches) > 0:
            return matches
        else:
            return None

    def remove_or_color_synctex_tags(self):
        for tag_count in list(self.synctex_highlight_tags):
            item = self.synctex_highlight_tags[tag_count]
            time_factor = time.time() - item['time']
            if time_factor > 1.5:
                if time_factor <= 1.75:
                    opacity_factor = int(self.ease(1 - (time_factor - 1.5) * 4) * 20)
                    item['tag'].set_property('background-rgba', self.color_manager.get_rgba(0.976, 0.941, 0.420, opacity_factor * 0.03))
                else:
                    start = self.source_buffer.get_start_iter()
                    end = self.source_buffer.get_end_iter()
                    self.source_buffer.remove_tag(item['tag'], start, end)
                    self.source_buffer.get_tag_table().remove(item['tag'])
                    del(self.synctex_highlight_tags[tag_count])
        return bool(self.synctex_highlight_tags)

    def ease(self, factor): return (factor - 1)**3 + 1

    def place_cursor(self, line_number, offset=0):
        text_iter = self.source_buffer.get_iter_at_line_offset(line_number, offset)
        self.source_buffer.place_cursor(text_iter)

    def get_cursor_offset(self):
        return self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_offset()

    def get_cursor_line_offset(self):
        return self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line_offset()

    def get_cursor_line_number(self):
        return self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()

    def cursor_ends_word(self):
        return self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).ends_word()

    def cut(self):
        self.copy()
        self.delete_selection()

    def copy(self):
        text = self.get_selected_text()
        if text != None:
            clipboard = self.source_view.get_clipboard(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)

    def paste(self):
        self.source_view.emit('paste-clipboard')

    def delete_selection(self):
        self.source_buffer.delete_selection(True, True)

    def select_all(self, widget=None):
        self.source_buffer.select_range(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter())

    def get_modified(self):
        return self.source_buffer.get_modified()

    def set_modified(self, modified):
        self.source_buffer.set_modified(modified)

    def undo(self):
        self.source_buffer.undo()

    def redo(self):
        self.source_buffer.redo()

    def scroll_cursor_onscreen(self):
        text_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        visible_lines = self.get_number_of_visible_lines()
        iter_position = self.source_view.get_iter_location(text_iter).y
        end_yrange = self.source_view.get_line_yrange(self.source_buffer.get_end_iter())
        buffer_height = end_yrange.y + end_yrange.height
        line_height = self.font_manager.get_line_height()
        window_offset = self.source_view.get_visible_rect().y
        window_height = self.source_view.get_visible_rect().height
        gap = min(math.floor(max((visible_lines - 2), 0) / 2), 5)
        if iter_position < window_offset + gap * line_height:
            scroll_iter = self.source_view.get_iter_at_location(0, max(iter_position - gap * line_height, 0)).iter
            self.source_buffer.move_mark(self.mover_mark, scroll_iter)
            self.source_view.scroll_to_mark(self.mover_mark, 0, False, 0, 0)
            return
        gap = min(math.floor(max((visible_lines - 2), 0) / 2), 8)
        if iter_position > (window_offset + window_height - (gap + 1) * line_height):
            scroll_iter = self.source_view.get_iter_at_location(0, min(iter_position + gap * line_height, buffer_height)).iter
            self.source_buffer.move_mark(self.mover_mark, scroll_iter)
            self.source_view.scroll_to_mark(self.mover_mark, 0, False, 0, 0)

    def get_number_of_visible_lines(self):
        line_height = self.font_manager.get_line_height()
        return math.floor(self.source_view.get_visible_rect().height / line_height)

    def get_bibitems(self):
        return self.symbols['bibitems']

    def add_packages(self, packages):
        first_package = True
        text = ''
        for packagename in packages:
            if not first_package: text += '\n'
            text += '\\usepackage{' + packagename + '}'
            first_package = False
        
        package_data = self.get_package_details()
        if package_data:
            max_end = 0
            for package in package_data.items():
                if package[1].end() > max_end:
                    max_end = package[1].end()
            insert_iter = self.source_buffer.get_iter_at_offset(max_end)
            if not insert_iter.ends_line():
                insert_iter.forward_to_line_end()
            self.insert_text_at_iter(insert_iter, '\n' + text)
        else:
            end_iter = self.source_buffer.get_end_iter()
            result = end_iter.backward_search('\\documentclass', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
            if result != None:
                result[0].forward_to_line_end()
                self.insert_text_at_iter(result[0], '\n' + text)
            else:
                self.insert_text_at_cursor(text)

    def remove_packages(self, packages):
        packages_dict = self.get_package_details()
        for package in packages:
            try:
                match_obj = packages_dict[package]
            except KeyError: return
            start_iter = self.source_buffer.get_iter_at_offset(match_obj.start())
            end_iter = self.source_buffer.get_iter_at_offset(match_obj.end())
            text = self.source_buffer.get_text(start_iter, end_iter, False)
            if text == match_obj.group(0):  
                if start_iter.get_line_offset() == 0:
                    start_iter.backward_char()
                self.source_buffer.delete(start_iter, end_iter)

    def get_packages(self):
        return self.symbols['packages']

    def get_package_details(self):
        return self.symbols['packages_detailed']

    def get_blocks(self):
        return self.symbols['blocks']

    def set_blocks(self, blocks):
        self.symbols['blocks'] = blocks

    def get_included_latex_files(self):
        return self.symbols['included_latex_files']

    def get_bibliography_files(self):
        return self.symbols['bibliographies']

    def get_labels(self):
        return self.symbols['labels']


