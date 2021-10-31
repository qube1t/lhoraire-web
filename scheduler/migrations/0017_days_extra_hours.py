# Generated by Django 3.1.6 on 2021-09-03 02:15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0016_taskinfo_to_reschedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="days",
            name="extra_hours",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=4
            ),
        ),
    ]
