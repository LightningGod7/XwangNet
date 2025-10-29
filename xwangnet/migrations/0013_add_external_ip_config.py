# Generated manually for external IP binding configuration
# Migration to add external IP fields to NetworkConfiguration,
# edge device fields to DeployedContainer, and EdgeDeviceNATRule model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('xwangnet', '0012_remove_log_file_path'),
    ]

    operations = [
        # Add external IP configuration fields to NetworkConfiguration
        migrations.AddField(
            model_name='networkconfiguration',
            name='use_external_ip',
            field=models.BooleanField(default=False, help_text='Bind edge device to external IP'),
        ),
        migrations.AddField(
            model_name='networkconfiguration',
            name='external_interface',
            field=models.CharField(blank=True, help_text='Interface name (e.g., macvlan0)', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='networkconfiguration',
            name='external_ip',
            field=models.GenericIPAddressField(blank=True, help_text='External IP address', null=True),
        ),
        migrations.AddField(
            model_name='networkconfiguration',
            name='create_new_interface',
            field=models.BooleanField(default=False, help_text='Create new macvlan interface'),
        ),
        migrations.AddField(
            model_name='networkconfiguration',
            name='use_dhcp',
            field=models.BooleanField(default=True, help_text='Use DHCP for IP assignment'),
        ),
        
        # Add edge device fields to DeployedContainer
        migrations.AddField(
            model_name='deployedcontainer',
            name='is_edge_device',
            field=models.BooleanField(default=False, help_text='Designated as edge device for external access'),
        ),
        migrations.AddField(
            model_name='deployedcontainer',
            name='edge_accessible',
            field=models.BooleanField(default=False, help_text='NAT configured and externally accessible'),
        ),
        
        # Create EdgeDeviceNATRule model
        migrations.CreateModel(
            name='EdgeDeviceNATRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('macvlan_interface', models.CharField(help_text='Macvlan interface name', max_length=50)),
                ('lan_ip', models.GenericIPAddressField(help_text='External LAN IP address')),
                ('internal_ip', models.GenericIPAddressField(help_text='Internal Docker container IP')),
                ('iptables_rules', models.JSONField(default=list, help_text='List of active iptables rules')),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('deployment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nat_rules', to='xwangnet.deployment')),
                ('edge_container', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nat_rules', to='xwangnet.deployedcontainer')),
            ],
            options={
                'unique_together': {('deployment', 'edge_container')},
            },
        ),
    ]


