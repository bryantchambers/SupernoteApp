from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django import forms
from .models import Book, Item, Calendar
from .recognition import valid_local


class BookForm(forms.ModelForm):
    class Meta:
        model = Book
        fields = ['title', 'start', 'timezone']
        widgets = {'start': forms.DateInput(attrs={'type': 'date'})}

    def clean_start(self):
        day = self.cleaned_data['start']
        if day.day != 1 or not 1970 <= day.year <= 2098:
            raise forms.ValidationError('Choose the first day of a month, between 1970 and 2098.')
        return day

    def clean_timezone(self):
        value = self.cleaned_data['timezone']
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise forms.ValidationError('Use an IANA timezone, such as Europe/Copenhagen.')
        return value


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ['kind', 'title', 'day', 'end_day', 'start_time', 'end_time', 'calendar', 'completed']
        widgets = {'day': forms.DateInput(attrs={'type': 'date'}), 'end_day': forms.DateInput(attrs={'type': 'date'}), 'start_time': forms.TimeInput(attrs={'type': 'time'}), 'end_time': forms.TimeInput(attrs={'type': 'time'})}

    def __init__(self, *args, book, **kwargs):
        super().__init__(*args, **kwargs)
        self.book = book
        self.fields['calendar'].queryset = Calendar.objects.filter(connection__owner=book.owner, selected=True, writable=True)
        if not self.is_bound and self.instance._state.adding and 'calendar' not in self.initial:
            self.initial['calendar'] = self.fields['calendar'].queryset.filter(kind=self.initial.get('kind', 'event'), is_default=True).values_list('pk', flat=True).first()

    def clean(self):
        data = super().clean()
        day, start, end = data.get('day'), data.get('start_time'), data.get('end_time')
        if day and not self.book.start <= day < date.fromisoformat(self.book.manifest['end']):
            raise forms.ValidationError('Choose a date inside this planner.')
        if bool(start) != bool(end) or (start and end and end <= start):
            raise forms.ValidationError('Provide both times, with the end after the start.')
        if start and day:
            try:
                valid_local(day, start, self.book.timezone)
                valid_local(day, end, self.book.timezone)
            except ValueError as exc:
                raise forms.ValidationError(str(exc))
        if data.get('kind') == 'task' and (start or data.get('end_day')):
            raise forms.ValidationError('Tasks have a due date, without appointment times.')
        if data.get('end_day') and (start or data['end_day'] < day):
            raise forms.ValidationError('An all-day end date must follow its start date.')
        cal = data.get('calendar')
        if cal and cal.kind != data.get('kind'):
            raise forms.ValidationError('Choose a matching calendar or task list.')
        return data
