# Generated by Django 3.1.6 on 2021-08-31 03:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0015_auto_20210821_0642"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskinfo",
            name="to_reschedule",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=4
            ),
            preserve_default=False,
        ),
    ]
