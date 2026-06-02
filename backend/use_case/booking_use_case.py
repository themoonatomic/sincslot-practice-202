from abc import abstractmethod, ABC
from collections import Counter
from datetime import datetime, timedelta, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.entity.booking import BookingEntity
from backend.entity.service import ServiceEntity
from backend.repository.booking_repository import IBookingRepository
from backend.repository.company_repository import ICompanyRepository
from backend.repository.service_repository import IServiceRepository
from backend.core.config import CalendarSchedule
from backend.core.metrics import booking_created_total


class IBookingUseCase(ABC):

    @abstractmethod
    async def save_booking(
            self,
            session: AsyncSession,
            service_id: int,
            client_id: int,
            time_start: datetime,
            time_end: datetime):
        raise NotImplemented

    @abstractmethod
    async def get_calendar_schedule_booking(self, session: AsyncSession, service_id: int, company_id: int,
                                            work_schedule: list):
        raise NotImplemented

    @abstractmethod
    async def get_booking_by_client(self, session: AsyncSession, client_id: int) -> list[BookingEntity] | None:
        raise NotImplemented

    @abstractmethod
    async def is_booking_time_in_work_schedule(
            self,
            session: AsyncSession,
            company_id: int,
            time_start: datetime,
            time_end: datetime,
    ):
        raise NotImplemented

    @abstractmethod
    async def get_booking_by_service_id_and_client_id(
            self,
            session: AsyncSession,
            service_id: int,
            client_id: int
    ) -> BookingEntity | None:
        raise NotImplemented

    @abstractmethod
    async def update_booking_by_id(
            self,
            session: AsyncSession,
            booking_id: int,
            status: str,
    ) -> BookingEntity | None:
        raise NotImplemented

    async def get_booking_by_id(self, session: AsyncSession, booking_id: int):
        raise NotImplemented


class BookingUseCase(IBookingUseCase):

    def __init__(
            self,
            booking_repository: IBookingRepository,
            service_repository: IServiceRepository,
            company_repository: ICompanyRepository,
            calendar_schedule_settings: CalendarSchedule
    ):
        self.service_repository = service_repository
        self.company_repository = company_repository
        self.calendar_schedule_settings = calendar_schedule_settings
        self.booking_repository = booking_repository

    async def save_booking(
            self,
            session: AsyncSession,
            service_id: int,
            client_id: int,
            time_start: datetime,
            time_end: datetime):
        booking = await self.booking_repository.save_booking(
            session,
            service_id,
            client_id,
            time_start,
            time_end,
        )

        booking_created_total.inc()

        return booking

    async def is_booking_time_in_work_schedule(
            self,
            session: AsyncSession,
            company_id: int,
            time_start: datetime,
            time_end: datetime,
    ):
        work_schedule = await self.company_repository.get_work_schedule_by_company_id(session, company_id)

        for ws in work_schedule:
            work_start = datetime.strptime(ws["work_start"], "%H:%M").time()
            work_end = datetime.strptime(ws["work_end"], "%H:%M").time()

            if not work_start <= time(time_start.hour, time_start.minute) < work_end:
                return False

            if not work_start < time(time_end.hour, time_end.minute) <= work_end:
                return False

        return True

    @staticmethod
    def get_work_schedule_by_day_of_week(work_schedule, day_of_week: int):
        for ws in work_schedule:
            if ws["day_of_week"] == day_of_week:
                return ws

    @staticmethod
    def get_intervals(
            start_time: time,
            end_time: time,
            service_duration_min: int
    ) -> list[tuple[time, time]]:

        if end_time <= start_time:
            raise ValueError("end_time должно быть строго позже start_time")
        if service_duration_min <= 0:
            raise ValueError("Длительность услуги должна быть положительной")

        # Переводим start_time и end_time в объекты datetime для удобства арифметики
        # Берём произвольную дату (например, сегодня), чтобы работать с datetime
        today = datetime.now().date()
        start_dt = datetime.combine(today, start_time)
        end_dt = datetime.combine(today, end_time)

        intervals = []
        current_time = start_dt

        while current_time + timedelta(minutes=service_duration_min) <= end_dt:
            # Формируем интервал: от current_time до current_time + длительность
            end_interval = current_time + timedelta(minutes=service_duration_min)
            # Формат строки: "ЧЧ:ММ–ЧЧ:ММ"
            # interval_str = current_time.strftime("%H:%M") + "–" + end_interval.strftime("%H:%M")
            intervals.append(
                (
                    time(current_time.time().hour, current_time.time().minute),
                    time(end_interval.time().hour, end_interval.time().minute)
                )
            )
            # Переходим к следующему интервалу
            current_time = end_interval

        return intervals

    @staticmethod
    def remove_overlapping_intervals(data):
        """
        Принимает словарь вида {ключ: [(start1, end1), (start2, end2), ...]}
        и возвращает словарь с теми же ключами, но с непересекающимися и уникальными интервалами
        в каждом списке (в порядке возрастания start).

        Для каждого ключа:
        - полностью удаляются интервалы, которые встречаются 2+ раза;
        - оставшиеся интервалы сортируются по началу;
        - последовательно добавляются в результат, если не пересекаются с последним добавленным.
        """
        result = {}

        for key, intervals in data.items():
            if not intervals:
                result[key] = []
                continue

            # Подсчитываем количество вхождений каждого интервала
            count = {}
            for interval in intervals:
                count[interval] = count.get(interval, 0) + 1

            # Оставляем только интервалы, которые встретились ровно 1 раз
            unique_intervals = [interval for interval in intervals if count[interval] == 1]

            # Если после удаления дубликатов ничего не осталось — записываем пустой список
            if not unique_intervals:
                result[key] = []
                continue

            # Сортируем интервалы по началу
            sorted_intervals = sorted(unique_intervals, key=lambda x: x[0])

            # Первый интервал всегда добавляем
            non_overlapping = [sorted_intervals[0]]

            for current in sorted_intervals[1:]:
                last = non_overlapping[-1]
                # Проверяем пересечение: если начало текущего >= концу последнего — не пересекаются
                if current[0] >= last[1]:
                    non_overlapping.append(current)

            result[key] = non_overlapping

        return result

    async def get_services_intervals(self, services: list[ServiceEntity], work_schedule: list) -> dict[int, list[tuple[time, time]]]:

        intervals = {}

        for ws in work_schedule:
            work_start = datetime.strptime(ws.get("work_start"), "%H:%M").time()
            work_end = datetime.strptime(ws.get("work_end"), "%H:%M").time()
            for service in services:
                list_intervals = self.get_intervals(work_start, work_end, service.duration)
                day_of_week = ws["day_of_week"]
                intervals[day_of_week] = list_intervals

        return intervals

    @staticmethod
    async def filter_intervals_by_service_duration(
            intervals: dict[int, list[tuple[time, time]]],
            service_duration_min: int) -> dict[int, list[tuple[time, time]]]:

        if service_duration_min <= 0:
            raise ValueError("Длительность услуги должна быть положительной")

        result_dict = {}

        for key in intervals:
            result = []
            for start, end in intervals[key]:
                # Преобразуем time в datetime для арифметики (берём произвольную дату)
                dummy_date = datetime.now().date()  # можно любую дату
                start_dt = datetime.combine(dummy_date, start)
                end_dt = datetime.combine(dummy_date, end)

                # Проверяем, что конец >= начало
                if end_dt < start_dt:
                    raise ValueError(f"Некорректный интервал: {start}–{end} (конец раньше начала)")

                # Вычисляем длительность интервала в минутах
                duration = (end_dt - start_dt).total_seconds() / 60

                # Если длительность точно равна заданной — добавляем в результат
                if duration == service_duration_min:
                    result.append({
                        "start": start.strftime("%H:%M"),
                        "end": end.strftime("%H:%M"),
                    })

            result_dict[key] = result

        return result_dict

    async def get_calendar_schedule_booking(self, session: AsyncSession, service_id: int, company_id: int,
                                            work_schedule: list):

        schedule: list[dict] = []

        service = await self.service_repository.get_service_by_id(session, service_id)
        if service is None:
            return

        services = await self.service_repository.get_services_by_company_id(session, company_id)

        services_intervals = await self.get_services_intervals(services, work_schedule)

        bookings_by_service_id = await self.booking_repository.get_booking_by_service_id(session, service_id)

        days_already_booked = [d.time_start.day for d in bookings_by_service_id]

        for booking in bookings_by_service_id:

            if booking.client_id is None:
                continue

            t = (time(hour=booking.time_start.time().hour, minute=booking.time_start.time().minute), time(hour=booking.time_end.time().hour, minute=booking.time_end.time().minute))
            day_of_week = booking.time_start.isoweekday()

            if day_of_week in services_intervals:
                services_intervals[day_of_week].append(t)

        non_overlapping_intervals = self.remove_overlapping_intervals(services_intervals)

        days_of_week_to_work = {w["day_of_week"] for w in work_schedule}

        for day in range(self.calendar_schedule_settings.calendar_schedule_limit_days):
            now = datetime.now() + timedelta(days=day)
            if now.isoweekday() in days_of_week_to_work:

                if now.day in days_already_booked:
                    time_to_book = await self.filter_intervals_by_service_duration(non_overlapping_intervals, service.duration),
                    time_to_book = time_to_book[0][now.isoweekday()] if isinstance(time_to_book, tuple) else time_to_book[now.isoweekday()]

                    new_time_to_book = []

                    for ws in work_schedule:
                        if ws["day_of_week"] == now.isoweekday():
                            work_start = ws["work_start"]
                            work_end = ws["work_end"]
                            for t in time_to_book:
                                time_to_book_start = t["start"]
                                time_to_book_end = t["end"]

                                if time_to_book_start < work_start or time_to_book_end > work_end:
                                    continue
                                else:
                                    new_time_to_book.append(t)

                    schedule.append({
                        "month": now.month,
                        "day": now.day,
                        "dayOfWeek": now.isoweekday(),
                        "timeToBook": new_time_to_book,
                        "isWork": True
                    })
                else:

                    result = []

                    intervals = non_overlapping_intervals.get(now.isoweekday())
                    if intervals is not None:
                        for interval in non_overlapping_intervals.get(now.isoweekday()):

                            for ws in work_schedule:
                                day_of_week = ws["day_of_week"]
                                work_start = ws["work_start"]
                                work_end = ws["work_end"]

                                if day_of_week != now.isoweekday():
                                    continue

                                work_start = time(int(work_start.split(":")[0]), int(work_start.split(":")[1]))
                                work_end = time(int(work_end.split(":")[0]), int(work_end.split(":")[1]))

                                work_start_dt = datetime.combine(datetime.today().date(), work_start)
                                work_end_dt = datetime.combine(datetime.today().date(), work_end)

                                work_start = int(work_start_dt.timestamp())
                                work_end = int(work_end_dt.timestamp())

                                start = int(datetime.combine(datetime.today().date(), time(interval[0].hour, interval[0].minute)).timestamp())
                                end = int(datetime.combine(datetime.today().date(), time(interval[1].hour, interval[1].minute)).timestamp())

                                if start < work_start or end > work_end:
                                    continue
                                else:
                                    result.append({
                                        "start": interval[0].strftime("%H:%M"),
                                        "end": interval[1].strftime("%H:%M")
                                    })

                    schedule.append({
                        "month": now.month,
                        "day": now.day,
                        "dayOfWeek": now.isoweekday(),
                        "timeToBook": result,
                        "isWork": True
                    })

            else:
                schedule.append({
                    "month": now.month,
                    "day": now.day,
                    "dayOfWeek": now.isoweekday(),
                    "timeToBook": [],
                    "isWork": False
                })

        return {
            **service.to_dict(),
            "schedule": schedule
        }

    async def get_booking_by_client(self, session: AsyncSession, client_id: int) -> list[BookingEntity] | None:
        return await self.booking_repository.get_booking_by_client(session, client_id)

    async def get_booking_by_service_id_and_client_id(
            self,
            session: AsyncSession,
            service_id: int,
            client_id: int
    ) -> BookingEntity | None:
        return await self.booking_repository.get_booking_by_service_id_and_client_id(session, service_id, client_id)

    async def update_booking_by_id(
            self,
            session: AsyncSession,
            booking_id: int,
            status: str,
    ) -> BookingEntity | None:
        data_to_update = {"status": status}
        return await self.booking_repository.update_booking_by_id(session, booking_id, data_to_update)

    async def get_booking_by_id(self, session: AsyncSession, booking_id: int):
        return await self.booking_repository.get_booking_by_id(session, booking_id)
