"""Administrator management routes."""

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.authz import ROLE_LABELS, role_required
from app.blueprints.admin import bp
from app.blueprints.admin.forms import (
    ActionForm,
    NamedRecordCreateForm,
    NamedRecordEditForm,
    TemporaryPasswordForm,
    UserCreateForm,
    UserEditForm,
)
from app.extensions import db
from app.models import AuditLog, Room, SchoolClass, User, UserRole
from app.user_validation import normalize_full_name, normalize_username

GENERIC_ERROR = "Unable to save the change. Please try again."


def normalize_name(value):
    """Normalize non-account class and room names."""
    return " ".join(value.strip().split())


def active_class_choices():
    classes = db.session.scalars(
        db.select(SchoolClass)
        .where(SchoolClass.is_active.is_(True))
        .order_by(SchoolClass.name)
    ).all()
    return [(0, "Not applicable"), *((item.id, item.name) for item in classes)]


def configure_user_form(form):
    form.class_id.choices = active_class_choices()


def lock_school_class(class_id):
    """Lock and return one class for transactional monitor validation."""
    if not class_id:
        return None
    return db.session.scalar(
        db.select(SchoolClass)
        .where(SchoolClass.id == class_id)
        .with_for_update()
    )


def add_audit(action, entity_type, entity_id, details=None):
    db.session.add(
        AuditLog(
            actor_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def commit_or_flash():
    try:
        db.session.commit()
    except (IntegrityError, SQLAlchemyError, ValueError):
        db.session.rollback()
        flash(GENERIC_ERROR, "danger")
        return False
    return True


def username_available(username, excluded_id=None):
    statement = db.select(User.id).where(User.username == username)
    if excluded_id is not None:
        statement = statement.where(User.id != excluded_id)
    return db.session.scalar(statement) is None


def validate_monitor_class(form):
    if form.role.data != UserRole.MONITOR.value:
        return None
    school_class = lock_school_class(form.class_id.data)
    if school_class is None or not school_class.is_active:
        form.class_id.errors.append("An active class is required for a monitor.")
        return None
    return school_class


def lock_active_admins():
    return db.session.scalars(
        db.select(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .with_for_update()
    ).all()


@bp.get("/")
@role_required(UserRole.ADMIN)
def dashboard():
    return render_template(
        "admin/dashboard.html",
        active_users=db.session.scalar(
            db.select(db.func.count(User.id)).where(User.is_active.is_(True))
        ),
        inactive_users=db.session.scalar(
            db.select(db.func.count(User.id)).where(User.is_active.is_(False))
        ),
        active_classes=db.session.scalar(
            db.select(db.func.count(SchoolClass.id)).where(
                SchoolClass.is_active.is_(True)
            )
        ),
        active_rooms=db.session.scalar(
            db.select(db.func.count(Room.id)).where(Room.is_active.is_(True))
        ),
    )


@bp.get("/users")
@role_required(UserRole.ADMIN)
def users():
    records = db.session.scalars(db.select(User).order_by(User.full_name)).all()
    return render_template(
        "admin/users/list.html", users=records, role_labels=ROLE_LABELS
    )


@bp.route("/users/new", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def create_user():
    form = UserCreateForm()
    configure_user_form(form)
    if form.validate_on_submit():
        username = normalize_username(form.username.data)
        school_class = validate_monitor_class(form)
        if not username_available(username):
            form.username.errors.append("That username is already in use.")
        elif form.role.data == UserRole.MONITOR.value and school_class is None:
            pass
        else:
            user = User(
                username=username,
                full_name=normalize_full_name(form.full_name.data),
                role=UserRole(form.role.data),
                school_class=school_class,
                is_active=form.is_active.data,
                must_change_password=True,
                # Required model placeholder; set_password replaces it before flush.
                password_hash="pending",  # nosec B106
            )
            user.set_password(form.temporary_password.data)
            db.session.add(user)
            try:
                db.session.flush()
                add_audit(
                    "USER_CREATED",
                    "User",
                    user.id,
                    {"role": user.role.value, "active": user.is_active},
                )
                db.session.commit()
            except (IntegrityError, SQLAlchemyError, ValueError):
                db.session.rollback()
                flash(GENERIC_ERROR, "danger")
            else:
                flash("User created.", "success")
                return redirect(url_for("admin.users"))
    return render_template("admin/users/form.html", form=form, title="Create user")


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def edit_user(user_id):
    user = db.get_or_404(User, user_id)
    original_is_active = user.is_active
    form = UserEditForm(obj=user)
    configure_user_form(form)
    if not form.is_submitted():
        form.role.data = user.role.value
        form.class_id.data = user.class_id or 0
    if form.validate_on_submit():
        username = normalize_username(form.username.data)
        new_role = UserRole(form.role.data)
        school_class = validate_monitor_class(form)
        if not username_available(username, user.id):
            form.username.errors.append("That username is already in use.")
        elif user.id == current_user.id and new_role != UserRole.ADMIN:
            form.role.errors.append("You cannot remove your own Administrator role.")
        elif user.id == current_user.id and not form.is_active.data:
            form.is_active.errors.append("You cannot deactivate your own account.")
        elif new_role == UserRole.MONITOR and school_class is None:
            pass
        else:
            removing_active_admin = (
                user.is_active
                and user.role == UserRole.ADMIN
                and (new_role != UserRole.ADMIN or not form.is_active.data)
            )
            if removing_active_admin:
                active_admins = lock_active_admins()
                if len(active_admins) == 1:
                    message = "The last active Administrator must remain active."
                    if new_role != UserRole.ADMIN:
                        form.role.errors.append(message)
                    else:
                        form.is_active.errors.append(message)
                    return render_template(
                        "admin/users/form.html", form=form, title="Edit user", user=user
                    )
            if new_role == UserRole.MONITOR:
                user.role = new_role
                user.school_class = school_class
            else:
                user.class_id = None
                user.school_class = None
                user.role = new_role
            user.full_name = normalize_full_name(form.full_name.data)
            user.username = username
            user.is_active = form.is_active.data
            add_audit(
                "USER_EDITED",
                "User",
                user.id,
                {"role": user.role.value, "active": user.is_active},
            )
            if user.is_active != original_is_active:
                add_audit(
                    "USER_ACTIVATED" if user.is_active else "USER_DEACTIVATED",
                    "User",
                    user.id,
                )
            if commit_or_flash():
                flash("User updated.", "success")
                return redirect(url_for("admin.users"))
    return render_template(
        "admin/users/form.html", form=form, title="Edit user", user=user
    )


def set_user_active(user_id, active):
    user = db.get_or_404(User, user_id)
    form = ActionForm()
    if not form.validate_on_submit():
        abort(400)
    if user.is_active == active:
        flash("User already has the requested status.", "info")
        return redirect(url_for("admin.users"))
    if user.id == current_user.id and not active:
        flash("You cannot deactivate your own account.", "danger")
    else:
        if active and user.role == UserRole.MONITOR:
            school_class = lock_school_class(user.class_id)
            if school_class is None or not school_class.is_active:
                flash(
                    "This monitor cannot be activated without an active "
                    "assigned class.",
                    "danger",
                )
                return redirect(url_for("admin.users"))
        if not active and user.role == UserRole.ADMIN:
            active_admins = lock_active_admins()
            if len(active_admins) == 1:
                flash("The last active Administrator cannot be deactivated.", "danger")
                return redirect(url_for("admin.users"))
        user.is_active = active
        add_audit(
            "USER_ACTIVATED" if active else "USER_DEACTIVATED",
            "User",
            user.id,
        )
        if commit_or_flash():
            flash("User status updated.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/activate")
@role_required(UserRole.ADMIN)
def activate_user(user_id):
    return set_user_active(user_id, True)


@bp.post("/users/<int:user_id>/deactivate")
@role_required(UserRole.ADMIN)
def deactivate_user(user_id):
    return set_user_active(user_id, False)


@bp.route("/users/<int:user_id>/temporary-password", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def reset_temporary_password(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        abort(403)
    form = TemporaryPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.temporary_password.data)
        user.must_change_password = True
        add_audit("USER_PASSWORD_RESET", "User", user.id)
        if commit_or_flash():
            flash("A new temporary password was issued.", "success")
            return redirect(url_for("admin.users"))
    return render_template(
        "admin/users/password.html", form=form, managed_user=user
    )


def list_records(model, template):
    records = db.session.scalars(db.select(model).order_by(model.name)).all()
    return render_template(template, records=records)


def save_named_record(model, entity_type, record=None):
    form_class = NamedRecordEditForm if record else NamedRecordCreateForm
    form = form_class(obj=record)
    if form.validate_on_submit():
        name = normalize_name(form.name.data)
        duplicate = db.session.scalar(
            db.select(model.id).where(
                db.func.lower(model.name) == name.lower(),
                model.id != (record.id if record else 0),
            )
        )
        if duplicate is not None:
            form.name.errors.append(f"That {entity_type.lower()} name already exists.")
        else:
            action = f"{entity_type.upper()}_{'EDITED' if record else 'CREATED'}"
            record = record or model()
            record.name = name
            if form_class is NamedRecordCreateForm:
                record.is_active = form.is_active.data
            db.session.add(record)
            try:
                db.session.flush()
                add_audit(
                    action,
                    entity_type,
                    record.id,
                    {"name": record.name, "active": record.is_active},
                )
                db.session.commit()
            except (IntegrityError, SQLAlchemyError, ValueError):
                db.session.rollback()
                flash(GENERIC_ERROR, "danger")
            else:
                flash(f"{entity_type} saved.", "success")
                endpoint = "admin.classes" if model is SchoolClass else "admin.rooms"
                return redirect(url_for(endpoint))
    return render_template(
        "admin/named_form.html",
        form=form,
        title=f"{'Edit' if record else 'Create'} {entity_type.lower()}",
    )


@bp.get("/classes")
@role_required(UserRole.ADMIN)
def classes():
    return list_records(SchoolClass, "admin/classes/list.html")


@bp.route("/classes/new", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def create_class():
    return save_named_record(SchoolClass, "Class")


@bp.route("/classes/<int:record_id>/edit", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def edit_class(record_id):
    return save_named_record(
        SchoolClass, "Class", db.get_or_404(SchoolClass, record_id)
    )


@bp.get("/rooms")
@role_required(UserRole.ADMIN)
def rooms():
    return list_records(Room, "admin/rooms/list.html")


@bp.route("/rooms/new", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def create_room():
    return save_named_record(Room, "Room")


@bp.route("/rooms/<int:record_id>/edit", methods=["GET", "POST"])
@role_required(UserRole.ADMIN)
def edit_room(record_id):
    return save_named_record(Room, "Room", db.get_or_404(Room, record_id))


def set_named_record_active(model, entity_type, endpoint, record_id, active):
    record = db.get_or_404(model, record_id)
    form = ActionForm()
    if not form.validate_on_submit():
        abort(400)
    if record.is_active == active:
        flash(f"{entity_type} already has the requested status.", "info")
        return redirect(url_for(endpoint))
    if not active and model is SchoolClass:
        record = lock_school_class(record_id)
        if record is None:
            abort(404)
        assigned_monitor = db.session.scalar(
            db.select(User.id).where(
                User.role == UserRole.MONITOR,
                User.class_id == record.id,
                User.is_active.is_(True),
            )
        )
        if assigned_monitor is not None:
            flash(
                "A class assigned to an active monitor cannot be deactivated.",
                "danger",
            )
            return redirect(url_for(endpoint))
    record.is_active = active
    add_audit(
        f"{entity_type.upper()}_{'ACTIVATED' if active else 'DEACTIVATED'}",
        entity_type,
        record.id,
    )
    if commit_or_flash():
        flash(f"{entity_type} status updated.", "success")
    return redirect(url_for(endpoint))


@bp.post("/classes/<int:record_id>/activate")
@role_required(UserRole.ADMIN)
def activate_class(record_id):
    return set_named_record_active(
        SchoolClass, "Class", "admin.classes", record_id, True
    )


@bp.post("/classes/<int:record_id>/deactivate")
@role_required(UserRole.ADMIN)
def deactivate_class(record_id):
    return set_named_record_active(
        SchoolClass, "Class", "admin.classes", record_id, False
    )


@bp.post("/rooms/<int:record_id>/activate")
@role_required(UserRole.ADMIN)
def activate_room(record_id):
    return set_named_record_active(Room, "Room", "admin.rooms", record_id, True)


@bp.post("/rooms/<int:record_id>/deactivate")
@role_required(UserRole.ADMIN)
def deactivate_room(record_id):
    return set_named_record_active(Room, "Room", "admin.rooms", record_id, False)
