# Generated by Django 3.1.6 on 2021-08-13 23:45

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduler", "0008_taskinfo_start_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserInfo",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("time_zone", models.CharField(max_length=5)),
                (
                    "week_day_work",
                    models.DecimalField(decimal_places=2, max_digits=4),
                ),
                (
                    "max_week_day_work",
                    models.DecimalField(decimal_places=2, max_digits=4),
                ),
                (
                    "week_end_work",
                    models.DecimalField(decimal_places=2, max_digits=4),
                ),
                (
                    "max_week_end_work",
                    models.DecimalField(decimal_places=2, max_digits=4),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name="taskinfo",
            name="user",
            field=models.ForeignKey(
                default="",
                on_delete=django.db.models.deletion.CASCADE,
                to="scheduler.userinfo",
            ),
        ),
    ]
