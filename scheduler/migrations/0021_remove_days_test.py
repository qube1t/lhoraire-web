# Generated by Django 3.1.6 on 2021-10-02 05:56

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0020_auto_20211002_0532'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='days',
            name='test',
        ),
    ]