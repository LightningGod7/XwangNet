# Migration to add port_forwarding field to NetworkConfiguration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xwangnet', '0013_add_external_ip_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='networkconfiguration',
            name='port_forwarding',
            field=models.TextField(blank=True, help_text='Port forwarding rules (e.g., 80:8080,443:8443)', null=True),
        ),
    ]

