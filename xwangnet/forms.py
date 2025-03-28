from django import forms
from .models import DeviceTemplate, NetworkConfiguration

class NetworkConfigurationForm(forms.ModelForm):
    class Meta:
        model = NetworkConfiguration
        fields = ['name', 'isolated', 'subnet', 'gateway']

class ComposeGeneratorForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['devices'] = forms.ModelMultipleChoiceField(
            queryset=DeviceTemplate.objects.all(),
            widget=forms.CheckboxSelectMultiple,
            required=False
        )

    def clean(self):
        cleaned_data = super().clean()
        device_counts = {}
        
        for key, value in self.data.items():
            if key.startswith('device_count_'):
                device_id = int(key.replace('device_count_', ''))
                count = int(value)
                if count > 0:
                    device_counts[device_id] = count
        
        if not device_counts:
            raise forms.ValidationError("Please select at least one device")
        
        cleaned_data['device_counts'] = device_counts
        return cleaned_data