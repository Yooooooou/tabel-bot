"""ConversationHandler state constants."""

(
    # Добавление сотрудника
    ADD_NAME, ADD_PHONE, ADD_POSITION, ADD_SECTION,
    ADD_SCHEDULE, ADD_DAYS_OFF, ADD_START_DATE,
    # Редактирование сотрудника
    EDIT_SELECT_EMP, EDIT_FIELD, EDIT_VALUE,
    # Смена
    SHIFT_SELECT_EMP, SHIFT_SELECT_DATE, SHIFT_SELECT_VALUE,
    SHIFT_IS_REPLACE, SHIFT_REPLACE_FOR,
    # Финансы
    FIN_SELECT_EMP, FIN_TYPE, FIN_VALUE,
    # Увольнение
    FIRE_SELECT_EMP, FIRE_DATE,
    # Удаление
    DELETE_SELECT_EMP,
    # Добавление администратора
    NEW_ADMIN_ID,
) = range(22)
