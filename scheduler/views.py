from datetime import date, datetime
from math import floor
from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.forms.formsets import formset_factory
from django.forms.models import modelformset_factory
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
import pytz
import pprint
import decimal

from .forms import ReschedulerDateForm, TaskForm, UserInfoForm

from scheduler.lhoraire_scheduler.model import TaskModel
from scheduler.lhoraire_scheduler.reposition import Reposition
from scheduler.lhoraire_scheduler.filter import Filter, set_old_schedule
from scheduler.lhoraire_scheduler.helpers import *

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.parsers import JSONParser
from .models import Days, TaskInfo, UserInfo
from .serializers import DaysSerializer, TaskInfoSerializer
from rest_framework.response import Response
from django.template.defaulttags import register

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)


@register.filter
def to_date(strdate):
    return datetime.strptime(strdate, "%Y-%m-%d").strftime("%d/%m")


@register.filter
def to_day(strdate):
    return WEEKDAYS[datetime.strptime(strdate, "%Y-%m-%d").weekday()]


@register.filter
def readable_hrs(hour):
    hour = float(hour)
    if floor(hour) == 0:
        return str(round((hour - floor(hour)) * 60)) + " mins"
    else:
        if round((hour - floor(hour)) * 60) == 60:
            return str(floor(hour) + 1) + " hrs 0 mins"
        else:
            return (
                str(floor(hour))
                + " hrs "
                + str(round((hour - floor(hour)) * 60))
                + " mins"
            )


def get_local_date(userinfo):
    timezone = pytz.common_timezones[userinfo.time_zone]
    local_date = datetime.now(pytz.timezone(timezone)).date()
    return local_date


# return a dictionary containing old tasks of the user


def get_old_tasks(request, local_date):
    tasks = TaskInfo.objects.filter(user__user=request.user)

    if len(tasks):
        taskinfoserializer = TaskInfoSerializer(tasks, many=True)

        oldtasks = {
            f"{info['id']}": [
                float(info["hours_needed"]),
                info["gradient"],
                [
                    getDateDelta(info["start_date"]),
                    getDateDelta(info["due_date"]),
                ],
                0,
                getDateDelta(info["modified_date"]),
            ]
            for info in taskinfoserializer.data
            if getDateDelta(info["due_date"]) > getDateDelta(local_date)
        }
    else:
        oldtasks = {}
    return oldtasks


def get_old_schedule(request, oldtasks, localdate):
    # fetching existing schedule as json/dict,
    # so that it can be used by backend
    days = Days.objects.filter(user__user=request.user)
    daysserializer = DaysSerializer(days, many=True)
    exist_schedule_formated = {
        day["date"]: {
            "quots": {
                f"t{n}": k
                for n, k in json.loads(day["tasks_jsonDump"]).items()
                if n in oldtasks.keys()
            }
        }
        for day in daysserializer.data
    }

    for day, data in dict(exist_schedule_formated).items():
        if datetime.strptime(day, "%Y-%m-%d").date() - localdate < timedelta(
            1
        ):
            
            exist_schedule_formated.pop(day)
            continue

        if not data["quots"]:
            exist_schedule_formated.pop(day)

    extra_hours = {
        getDateDelta(day["date"]): day["extra_hours"]
        for day in daysserializer.data
    }
    return days, daysserializer, exist_schedule_formated, extra_hours


def run_algorithm(
    exist_schedule_formated, userinfo, newtask_cumulation, oldtasks
):
    # filtering the new tasks and old tasks to know which all tasks to be
    # included in the reschedule
    new_tasks_filtered = Filter(newtask_cumulation, oldtasks, {}, {}, {}, {})

    
    # performing backend schedule generation
    process = Reposition(
        new_tasks_filtered,
        exist_schedule_formated,
        oldtasks,
        (userinfo.week_day_work, userinfo.week_end_work),
        (userinfo.max_week_day_work, userinfo.max_week_end_work),
        {},
        local_date,
    )

    return process


def update_db(
    request,
    updated_tasks,
    final_schedule,
    final_to_reschedule,
    daysserializer,
    days,
    local_date,
    userinfo,
    extrahours,
):
    # updating the new and old tasks that were used with refreshed start dates

    show_to_resch_alert = 0

    for task, data in updated_tasks.items():
        taskobj = TaskInfo.objects.get(id=task)
        taskobj.start_date = date.fromisoformat(getDatefromDelta(data[0]))
        taskobj.modified_date = local_date
        if not taskobj.to_reschedule and final_to_reschedule.get(task, 0):
            show_to_resch_alert += 1
        taskobj.to_reschedule = final_to_reschedule.get(task, 0)
        taskobj.save()

    if show_to_resch_alert:
        messages.warning(
            request,
            "Some tasks could not be scheduled. These tasks can be viewed by \
clicking on the top right Unschedulable Tasks button. <br> \
You can: <br> \
add extra hours to days by clicking the + button on hovering on them <br> \
or change the due date or hours needed in \
the All Tasks page for these tasks.",
        )

    # format to save the data
    new_schedule_reformated = [
        {
            "date": datestr,
            "tasks_jsonDump": {
                n.strip("t"): round(k, 3) for n, k in info["quots"].items()
            },
            "user": userinfo,
            "extra_hours": extrahours.get(getDateDelta(datestr), 0),
        }
        for datestr, info in final_schedule.items()
    ]

    
    # updates days info
    daysserializer.update(days, new_schedule_reformated, local_date)


# the process of running the algorithm and updating the database
def process(
    request,
    userinfo,
    oldtasks=None,
    newtask_cumulation={},
    reschedule_range={},
):
    # gets the local date of the user
    local_date = get_local_date(userinfo)

    # if there is no oldtasks given, get them.
    if oldtasks is None:
        oldtasks = get_old_tasks(request, local_date)

    # getting existing schedule and activating the days serializer

    days = get_old_schedule(request, oldtasks, local_date)[0]
    daysserializer = get_old_schedule(request, oldtasks, local_date)[1]
    exist_schedule_formated = get_old_schedule(request, oldtasks, local_date)[
        2
    ]


    # getting the extra hours
    extra_hours = get_old_schedule(request, oldtasks, local_date)[3]

    man_reschedule = False
    if reschedule_range:
        man_reschedule = True

    

    new_tasks_filtered, used_day_ranged = Filter(
        newtask_cumulation,
        oldtasks,
        man_reschedule,
        reschedule_range,
        local_date,
        float(userinfo.week_day_work),
    )

    old_schedule = set_old_schedule(
        exist_schedule_formated,
        used_day_ranged,
        float(userinfo.week_day_work),
        float(userinfo.week_end_work),
        float(userinfo.max_week_day_work),
        float(userinfo.max_week_end_work),
        extra_hours,
    )
    # extra_hours = get_old_schedule(request, oldtasks)[3]

    
    # performing backend schedule generation
    schedule = Reposition(
        new_tasks_filtered,
        old_schedule,
        oldtasks,
        (userinfo.week_day_work, userinfo.week_end_work),
        (userinfo.max_week_day_work, userinfo.max_week_end_work),
        extra_hours,
        local_date,
    )
    

    # results of backend
    final_schedule = schedule.schedule  # schedule as dict
    # task start_date and excess data (if any) as list
    updated_tasks = schedule.worked_tasks()

    final_to_reschedule = schedule.to_reschedule

    update_db(
        request,
        updated_tasks,
        final_schedule,
        final_to_reschedule,
        daysserializer,
        days,
        local_date,
        userinfo,
        extra_hours,
    )


# function to add tasks or return the form
# @login_required(login_url='/accounts/login/')


def add_tasks(request, internal=False):
    TaskFormSet = formset_factory(TaskForm, extra=1)

    # if there was a POST in the form
    if request.method == "POST":
        formset = TaskFormSet(request.POST)  # collection of all forms

        # check whether the formset is valid:
        if formset.is_valid():
            # getting the ONLY UserInfo obj of this user
            userinfo = UserInfo.objects.get(user=request.user)

            # fetching old tasks in json/dict format for the backend
            # to understand
            local_date = get_local_date(userinfo)
            oldtasks = get_old_tasks(request, local_date)
            # getting timezone of the user, so as to prevent schedule
            # miscalculations

            # fetching new tasks from the forms
            newtask_cumulation = {}

            for form in formset:
                # validates whether the limit of 8 tasks with same due date
                dues_work = TaskInfo.objects.filter(
                    due_date=form.cleaned_data['due_date'],
                    user__user = request.user
                )

                
                if len(dues_work) >= 8:
                    messages.warning(
                        request,
                        'Max limit of 8 tasks with same due date')
                    # redirect to a dashboard URL:
                    return HttpResponseRedirect("/scheduler/")

                obj = form.save(commit=False)
                obj.user = userinfo
                obj.to_reschedule = 0
                obj.total_hours = float(obj.hours_needed)
                obj.save()
                # forming TaskModel using the form data for backend
                task = TaskModel(
                    id=obj.id,
                    due=getDateDelta(obj.due_date),
                    work=float(obj.hours_needed),
                    week_day_work=float(userinfo.week_day_work),
                    days=0,
                    gradient=obj.gradient,
                    today=getDateDelta(local_date) + 1,
                )
                newtask_cumulation[
                    (
                        str(form.instance.pk),
                        obj.task_name,
                        getDateDelta(obj.due_date),
                    )
                ] = task

            process(request, userinfo, oldtasks, newtask_cumulation, {})

        else:
            messages.error(request, formset.errors)
            # redirect to a new URL:
        return HttpResponseRedirect("/scheduler/")

    # if a GET (or any other method) we'll create a blank form
    else:
        if internal:
            return TaskFormSet()
        else:
            return HttpResponseRedirect("/scheduler/")
    # return render(request, 'scheduler/create.html', {'formset': formset})


# removing previous days of the user and making the yesterday and today's
# readonly


def previous_days(earliest_day, user, local_date):
    day_count = int(
        (local_date - datetime.strptime(earliest_day, "%Y-%m-%d").date()).days
    )
    
    
    all_tasks_query = TaskInfo.objects.filter(user__user=user)

    if all_tasks_query.exists():
        # delete old tasks
        for task in all_tasks_query:
            # delete the task info if the due date is 1 days ago or before
            if (task.due_date - local_date).days <= 1:
                task.delete()

    task_to_remove = {}

    # goes through all the days prior to today
    for readonly_date in (
        local_date - timedelta(n) for n in range(day_count + 1)
    ):
        
        day_obj = Days.objects.filter(user__user=user, date=readonly_date)

        # if the day exists
        if day_obj.exists():
            day_tasks = json.loads(day_obj[0].tasks_jsonDump)

            #
            for task, hours in day_tasks.items():
                task_obj = TaskInfo.objects.filter(
                    user__user=user, id=int(task)
                )

                if task_obj.exists():
                    task_info = task_obj[0]
                    # comment
                    # preventing duplication of reduction in time.
                    if (local_date - task_info.modified_date).days > (
                        local_date - day_obj[0].date
                    ).days:
                        
                        #     task_info.modified_date,
                        #     local_date,
                        #     day_obj[0].date,
                        # )
                        task_to_remove[task] = (
                            task_to_remove.get(task, 0) + hours
                        )

            # day will be deleted if it is more than 2 days prior
            if (local_date - readonly_date).days > 2:
                day_obj.delete()

    # removing task hours from the task info
    for task, hours in task_to_remove.items():
        task_obj = TaskInfo.objects.filter(user__user=user, id=int(task))

        if task_obj.exists():
            task_info = task_obj[0]
            task_info.hours_needed -= decimal.Decimal(hours)
            task_info.modified_date = local_date
            task_info.start_date = local_date + timedelta(
                # (local_date - task_info.start_date).days
                1
            )
            task_info.save()

# view onto which reschedule form will POST to
def rescheduler(request, internal=False):
    
    if request.method == "POST":
        form = ReschedulerDateForm(request.POST)
        if form.is_valid():
            userinfo = UserInfo.objects.get(user=request.user)
            # from_date = form.cleaned_data['from_date']
            extra_hours = float(form.cleaned_data["extra_hours"])
            reschedule_date = form.cleaned_data["date"]
            

            day_obj_query = Days.objects.filter(
                user__user=request.user,
                date=date.fromisoformat(reschedule_date),
            )

            # checks if the day exists before adding hours to that day.
            if day_obj_query.exists():
                day_obj = day_obj_query[0]
                tasks = json.loads(day_obj.tasks_jsonDump)
                sum_of_tasks = sum(tasks.values())

                # backend validation that the sum of hours donot exceed 24hours
                if sum_of_tasks + extra_hours > 24:
                    extra_hours -= (sum_of_tasks + extra_hours) - 24

                if extra_hours < 0:
                    return HttpResponseRedirect("/scheduler/")

                day_obj.extra_hours = extra_hours
                day_obj.save()

                process(
                    request,
                    userinfo,
                    None,
                    {},
                    reschedule_range={
                        "0": (
                            getDateDelta(reschedule_date),
                            getDateDelta(reschedule_date),
                        )
                    },
                )

            return HttpResponseRedirect("/scheduler/")
    else:
        if internal:
            
            return ReschedulerDateForm()
        else:
            return HttpResponseRedirect("/scheduler/")



# view showing the dashboard

@login_required(login_url="/accounts/login/")
def index(request):
    # if user does not exist, redirect to initial info view
    if not UserInfo.objects.filter(user=request.user).exists():
        return redirect("/scheduler/initial-info")

    # get local date of user, schedule, and tasks
    user_query = UserInfo.objects.get(user=request.user)
    local_date = get_local_date(user_query)

    schedule_query = Days.objects.filter(user__user=request.user).order_by(
        "date"
    )

    tasks_query = TaskInfo.objects.filter(user__user=request.user)

    # if the user has active tasks
    if tasks_query.exists():

        for task in tasks_query:
            
            # delete the task info if the due date is today or before
            if (task.due_date - local_date).days <= 0:
                task.delete()

        # get tasks and non-schedulable tasks in python dictionary form
        taskinfoserializer = TaskInfoSerializer(tasks_query, many=True)

        tasks = {
            f"{info['id']}": [
                float(info["hours_needed"]),
                info["gradient"],
                [
                    getDateDelta(info["start_date"]),
                    getDateDelta(info["due_date"]),
                ],
                info["to_reschedule"],
                getDateDelta(info["modified_date"]),
                info["task_name"],
                info["color"],
                info["task_description"],
                float(info["total_hours"]),
            ]
            for info in taskinfoserializer.data
        }
        to_reschedule = {
            task: float(info[3])
            for task, info in tasks.items()
            if float(info[3]) != 0
        }

        # getting due days that are within 10 days and progress
        due_dates_comming_up = {
            task[5]: task[2][1] - getDateDelta(local_date)
            for task in tasks.values()
            if task[2][1] - getDateDelta(local_date) <= 10
            and task[2][1] - getDateDelta(local_date) > 0
        }

        due_dates_comming_up = dict(
            sorted(due_dates_comming_up.items(), key=lambda item: item[1])
        )

        progress_dict = {
            task[5]: round(1 - task[0] / task[8], 2)
            for task in tasks.values()
            if task[2][1] - getDateDelta(local_date) > 1
            and 1 - task[0] / task[8]
        }

        progress = dict(
            sorted(
                progress_dict.items(), key=lambda item: item[1], reverse=True
            )
        )

    else:
        # if the user does not have any tasks created
        tasks = {}
        to_reschedule = {}
        due_dates_comming_up = {}
        progress = {}

    # forms to be sent to the view
    add_tasks_formset = add_tasks(request=request, internal=True)
    rescheduleform = rescheduler(request=request, internal=True)

    # if user has a schedule:
    if schedule_query.exists():

        # get schedule in python dict form
        daysserializer = DaysSerializer(schedule_query, many=True)
        schedule = {
            day["date"]: {
                "quote": {
                    task: hours
                    for task, hours in json.loads(
                        day["tasks_jsonDump"]
                    ).items()
                    if task in tasks
                },
                "extra_hours": float(day["extra_hours"]),
            }
            for day in daysserializer.data
        }


        # schedule is valid
        if schedule:
            earliest_day = list(schedule.keys())[0]
            previous_days(earliest_day, request.user, local_date)

            # if days before yesterday exist, delete them
            for day, data in dict(schedule).items():
                if datetime.strptime(
                    day, "%Y-%m-%d"
                ).date() - local_date < timedelta(-1):
                    schedule.pop(day)
                    continue

            latest = Days.objects.filter(user__user=request.user).latest(
                "date"
            )

            # no of days
            day_count = int((latest.date - local_date).days) + 1

            for single_date in (
                local_date + timedelta(n - 1) for n in range(day_count)
            ):
                if single_date.strftime("%Y-%m-%d") not in list(
                    schedule.keys()
                ):
                    schedule[single_date.strftime("%Y-%m-%d")] = {
                        "quote": {"0": 0}
                    }

            schedule = dict(
                sorted(
                    schedule.items(),
                    key=lambda x: datetime.strptime(x[0], "%Y-%m-%d"),
                )
            )

            # pprint.pprint(schedule)

            last_day = list(schedule.keys())[-1]


            todays_todo = (
                schedule[local_date.strftime("%Y-%m-%d")]["quote"]
                if schedule[local_date.strftime("%Y-%m-%d")]["quote"]
                != {"0": 0}
                else {}
            )
        else:
            todays_todo = {}
            last_day = 0
    else:
        schedule = {}
        todays_todo = {}
        last_day = 0

    return render(
        request,
        "scheduler/dashboard.html",
        {
            "schedule": schedule,
            "tasks": tasks,
            "add_tasks_formset": add_tasks_formset,
            "userinfo": user_query,
            "todays_todo": todays_todo,
            "last_day": last_day,
            "upper_limit": user_query.max_week_end_work
            if user_query.max_week_end_work > user_query.max_week_day_work
            else user_query.max_week_day_work,
            "to_reschedule": to_reschedule,
            "reschedule_form": rescheduleform,
            "dues_comming_up": due_dates_comming_up,
            "progress": progress,
        },
    )


@login_required
def edit_tasks(request):
    TaskModelFormSet = modelformset_factory(
        TaskInfo,
        form=TaskForm,
        exclude=(
            "user",
            "id",
            "modified_date",
            "start_date",
            "days_needed",
            "to_reschedule",
        ),
        extra=0,
    )

    if request.method == "GET":
        add_tasks_formset = add_tasks(request=request, internal=True)

        formset = TaskModelFormSet(
            queryset=TaskInfo.objects.filter(user__user=request.user).order_by(
                "due_date"
            )
        )

        tasks_query = TaskInfo.objects.filter(user__user=request.user)
        
        if tasks_query.exists():
            taskinfoserializer = TaskInfoSerializer(tasks_query, many=True)

            tasks = {
                f"{info['id']}": [
                    float(info["hours_needed"]),
                    info["gradient"],
                    [
                        getDateDelta(info["start_date"]),
                        getDateDelta(info["due_date"]),
                    ],
                    info["to_reschedule"],
                    getDateDelta(info["modified_date"]),
                    info["task_name"],
                    info["color"],
                    info["task_description"],
                    float(info["total_hours"]),
                ]
                for info in taskinfoserializer.data
            }
            
            to_reschedule = {
                task: float(info[3])
                for task, info in tasks.items()
                if float(info[3]) != 0
            }

        else:
            tasks = {}
            to_reschedule = {}

        return render(
            request,
            "scheduler/edit.html",
            {
                "formset": formset,
                "add_tasks_formset": add_tasks_formset,
                "to_reschedule": to_reschedule,
                "tasks": tasks,
            },
        )

    else:
        formset = TaskModelFormSet(
            request.POST,
            queryset=TaskInfo.objects.filter(user__user=request.user),
        )

        userinfo = UserInfo.objects.get(user=request.user)
        local_date = get_local_date(userinfo)

        if formset.is_valid():
            oldtasks = get_old_tasks(request, local_date)
            updated_tasks = {}
            newtask_cumulation = {}
            needs_redo = 0

            for form in formset:
                if form.has_changed():
                    # id = form.cleaned_data['id']
                    obj = form.save()
                    obj.total_hours = float(obj.total_hours) - (
                        oldtasks[str(obj.id)][0] - float(obj.hours_needed)
                    )
                    obj.save()
                    
                    # algorithm is rerun only if any of these three are edited
                    if (
                        "due_date" in form.changed_data
                        or "hours_needed" in form.changed_data
                        or "gradient" in form.changed_data
                    ):
                        needs_redo += 1
                        updated_tasks[int(obj.id)] = oldtasks.pop(str(obj.id))

                        if float(obj.hours_needed):
                            task = TaskModel(
                                id=int(obj.id),
                                due=getDateDelta(obj.due_date),
                                work=float(obj.hours_needed),
                                week_day_work=float(userinfo.week_day_work),
                                days=0,
                                gradient=obj.gradient,
                                today=getDateDelta(local_date) + 1,
                            )
                            newtask_cumulation[
                                (
                                    f"{form.instance.pk}",
                                    (obj.task_name),
                                    getDateDelta(obj.due_date),
                                )
                            ] = task
                        else:
                            TaskInfo.objects.get(id=obj.id).delete()
            if needs_redo:
                n = 0
                for task, info in updated_tasks.items():
                    reschedule_range = {f"{n}": tuple(info[2])}
                    # info[2]
                    n -= 1

                process(
                    request,
                    userinfo,
                    oldtasks,
                    newtask_cumulation,
                    reschedule_range,
                )
        else:
            messages.error(request, formset.errors)
        return redirect("/scheduler/edit")


@api_view(["GET", "POST"])
def schedule(request):
    if request.user.is_authenticated:
        if request.method == "GET":
            days = Days.objects.filter(user__user=request.user).order_by(
                "date"
            )
            daysserializer = DaysSerializer(days, many=True)
            result = {
                (day["date"]): {
                    "quote": {
                        task: quotes
                        for task, quotes in json.loads(
                            day["tasks_jsonDump"]
                        ).items()
                    },
                    "date": getDateDelta(day["date"]),
                }
                for day in daysserializer.data
            }
            return Response(result)


@api_view(["GET", "POST"])
def tasks(request):
    if request.user.is_authenticated:
        if request.method == "GET":
            tasks = TaskInfo.objects.filter(user__user=request.user)
            taskinfoserializer = TaskInfoSerializer(tasks, many=True)
            result = {
                info["id"]: [
                    float(info["hours_needed"]),
                    info["gradient"],
                    [
                        (info["start_date"], getDateDelta(info["start_date"])),
                        (info["due_date"], getDateDelta(info["due_date"])),
                    ],
                    (
                        info["modified_date"],
                        getDateDelta(info["modified_date"]),
                    ),
                ]
                for info in taskinfoserializer.data
            }
            return Response(result)


@login_required
def userinfo(request):
    user_info = UserInfo.objects.filter(user=request.user)

    if request.method == "POST":
        if user_info.exists():
            time_zone = UserInfo.objects.get(user=request.user).time_zone
            form = UserInfoForm(
                request.POST, instance=UserInfo.objects.get(user=request.user)
            )

            # Form is valid
            if form.is_valid():
                # no change happens when time zone is tried to be changed, 
                # ie it cannot be changed
                if (
                    "week_day_work" in form.changed_data
                    or "max_week_day_work" in form.changed_data
                    or "week_end_work" in form.changed_data
                    or "max_week_end_work" in form.changed_data
                ):
                    if form.cleaned_data['week_day_work'] > 24\
                    or form.cleaned_data['max_week_day_work'] > 24\
                    or form.cleaned_data['week_end_work'] >24\
                    or form.cleaned_data['max_week_end_work'] > 24:
                        messages.warning(request, 'Daily limits need to be \
less than 24 hours.')
                        return HttpResponseRedirect("/scheduler/settings")
                        
                    if form.cleaned_data['week_day_work'] \
                    > form.cleaned_data['max_week_day_work'] \
                    or form.cleaned_data['week_end_work'] \
                    > form.cleaned_data['max_week_end_work']:
                        messages.warning(request, 'Max limits need to be \
greater than normal limits.')
                        return HttpResponseRedirect("/scheduler/settings")


                    local_date = get_local_date(user_info[0])
                    latest = (
                        Days.objects.filter(user__user=request.user)
                        .latest("date")
                        .date
                    )
                    
                    process(
                        request,
                        user_info[0],
                        None,
                        {},
                        reschedule_range={
                            "0": (
                                getDateDelta(local_date),
                                getDateDelta(latest),
                            )
                        },
                    )

                a = form.save(commit=False)
                if a.time_zone != time_zone:
                    messages.warning(request, 'Time Zones cannot be changed')
                a.time_zone = time_zone
                a.save()

            return redirect("/scheduler/settings")
        else:
            form = UserInfoForm(request.POST)

            if form.is_valid():

                # see if the inputs are valid
                if form.cleaned_data['week_day_work'] > 24\
                or form.cleaned_data['max_week_day_work'] > 24\
                or form.cleaned_data['week_end_work'] >24\
                or form.cleaned_data['max_week_end_work'] > 24:
                    messages.warning(request, 'Daily limits need to be \
less than 24 hours.')
                    return HttpResponseRedirect("/scheduler/settings")

                if form.cleaned_data['week_day_work'] \
                > form.cleaned_data['max_week_day_work'] \
                or form.cleaned_data['week_end_work'] \
                > form.cleaned_data['max_week_end_work']:
                    messages.warning(request, 'Max limits need to be greater \
than normal limits.')
                    return HttpResponseRedirect("/scheduler/initial-info")
                
                # saves and returns to dashboard
                a = form.save(commit=False)
                a.user = request.user
                a.save()
                return HttpResponseRedirect("/scheduler/")
    else:
        if user_info.exists():
            user_not_complete = False

            form = UserInfoForm(
                instance=UserInfo.objects.get(user=request.user)
            )

            add_tasks_formset = add_tasks(request=request, internal=True)

            tasks_query = TaskInfo.objects.filter(user__user=request.user)
            
            if tasks_query.exists():
                taskinfoserializer = TaskInfoSerializer(tasks_query, many=True)

                tasks = {
                    f"{info['id']}": [
                        float(info["hours_needed"]),
                        info["gradient"],
                        [
                            getDateDelta(info["start_date"]),
                            getDateDelta(info["due_date"]),
                        ],
                        info["to_reschedule"],
                        getDateDelta(info["modified_date"]),
                        info["task_name"],
                        info["color"],
                        info["task_description"],
                        float(info["total_hours"]),
                    ]
                    for info in taskinfoserializer.data
                }
                
                to_reschedule = {
                    task: float(info[3])
                    for task, info in tasks.items()
                    if float(info[3]) != 0
                }

            else:
                to_reschedule = {}
                add_tasks_formset = {}
                tasks = {}

        else:
            user_not_complete = True
            form = UserInfoForm()
            add_tasks_formset = {}
            to_reschedule = {}
            tasks = {}

        return render(
            request,
            "scheduler/user_info.html",
            {
                "form": form,
                "user_not_complete": user_not_complete,
                "add_tasks_formset": add_tasks_formset,
                "to_reschedule": to_reschedule,
                "tasks": tasks,
            },
        )
    # else:
    #     return redirect('/scheduler/')


# @login_required
# def settings(request):
#     if request.method == 'POST':
#         pass
#     else:
#         form
