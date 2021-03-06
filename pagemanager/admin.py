from functools import partial

from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.util import unquote
from django.contrib.contenttypes import generic
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.forms.widgets import Media
from django.http import HttpResponseRedirect
from django.utils.encoding import force_unicode

from pagemanager import PageAdmin
from pagemanager.app_settings import PAGEMANAGER_PAGE_MODEL, \
    PAGEMANAGER_PAGE_MODELADMIN
from pagemanager.forms import PageAdminFormMixin
from pagemanager.models import Page, PlaceholderPage, RedirectPage
from pagemanager import signals
from pagemanager.sites import pagemanager_site
from pagemanager.permissions import get_permissions, get_lookup_function


# PageAdmin located in pagemanager.init to prevent a circular import
admin.site.register(PAGEMANAGER_PAGE_MODEL, PAGEMANAGER_PAGE_MODELADMIN)

# Register stock pagemanager layouts
pagemanager_site.register([PlaceholderPage, RedirectPage])


class PageInline(generic.GenericStackedInline):
    """
    An inline for the Page model that's used in the PageLayout change_view.
    """
    can_delete = False
    ct_field = 'layout_type'
    ct_fk_field = 'object_id'
    extra = 1
    fieldsets = PageAdmin.fieldsets
    max_num = 1
    model = PAGEMANAGER_PAGE_MODEL
    template = 'pagemanager/admin/inlines/page_inline.html'
    page_inline = True

    def __init__(self, *args, **kwargs):
        super(PageInline, self).__init__(*args, **kwargs)

        class ComposedFormClass(PageAdminFormMixin, self.form):
            pass

        self.form = ComposedFormClass


class PageLayoutAdmin(admin.ModelAdmin):
    """
    Common admin for PageLayout subclasses.
    """
    change_form_template = 'pagemanager/admin/pagelayout_change_view.html'
    formfield_overrides = {}
    inlines = [PageInline]
    exclude = []
    verbose_name = None
    verbose_name_meta = None
    readonly_fields = []

    def changelist_view(self, request, extra_context=None):
        """
        Redirect the PageLayout changelist_view to the Page changelist_view
        """
        return HttpResponseRedirect(reverse('admin:index'))

    def _get_page_formset(self, request):
        for fs in self.get_formsets(request):
            if fs.model == Page:
                return fs
        return None

    def change_view(self, request, object_id, extra_context=None):
        """
        Adds some initial permissions checks to ensure that users can't:

            1. view objects they don't have permissions on
            2. change item's publication status without needed permissions
            3. change item's visibility status without needed permissions

        """
        obj = self.get_object(request, unquote(object_id))
        obj_pages = obj.page.all()
        if len(obj_pages) == 1:
            page = obj_pages[0]
        else:
            val = obj_pages and "Multiple" or "Zero"
            raise ValueError((
                "%s pages relate to this layout. Only one page can "
                "relate to this layout and at least one page must do so."
            )% val) 

        lookup_perm = get_lookup_function(request.user, get_permissions())
        # Reject users who don't have permission to view the page becuase
        # it's unpublished or invisible.
        if not page.is_visible() and not lookup_perm('view_private_pages'):
            # FIXME: remove details about exception after testing.
            raise PermissionDenied("Can't view invisible pages.")
        if not page.is_published() and not lookup_perm('view_draft_pages'):
            # FIXME: remove details about exception after testing.
            raise PermissionDenied("Can't view unpublished pages.")

        if request.method == 'POST':
            # If a user who doesn't have permissions to change is posting
            # data to this view, raise a PermissionDenied.
            if page.is_published() and not lookup_perm(
                'modify_published_pages'
            ):
                # FIXME: remove details about exception after testing.
                raise PermissionDenied("Can't modify published pages.")

            formset = self._get_page_formset(request)
            prefix = formset.get_default_prefix()
            ModelForm = self.get_form(request, obj)
            form = ModelForm(request.POST, request.FILES)
            changed_data = form.data
            opts = self.model._meta
            this_url = reverse(
                "admin:%s_%s_change" % (opts.app_label, opts.module_name),
                args=(object_id,)
            )

            def value_filter(name, prefix=None):
                def f(obj):
                    return obj.startswith(prefix) and obj.endswith(name)
                return f
            get_value_filter = partial(value_filter, prefix=prefix)

            # Verify that users can't change status if they don't have
            # permissions to do so.
            key_match = filter(get_value_filter('status'), changed_data)
            if key_match and changed_data[key_match[0]] != page.status:
                if not lookup_perm('change_status'):
                    message = (
                        "You don't have permission to change the status "
                        "of this page."
                    )
                    messages.add_message(request, messages.ERROR, message)
                    return HttpResponseRedirect(this_url)

            # Verify that users can't change visibility if they don't have
            # permissions to do so.
            key_match = filter(
                get_value_filter('visibility'),
                changed_data
            )
            if key_match and changed_data[key_match[0]] != page.visibility:
                if not lookup_perm('change_visibility'):
                    message = (
                        "You don't have permission to change the "
                        "visibility of this page."
                    )
                    messages.add_message(request, messages.ERROR, message)
                    return HttpResponseRedirect(this_url)
            # Fire the custom signal for page editing in the admin.
            signals.page_edited.send(sender=self, page=page, created=False)

        # All permissions checks have passed.
        return super(PageLayoutAdmin, self).change_view(
            request,
            object_id,
            extra_context=None
        )

    def add_view(self, request, form_url='', extra_context=None):
        """
        Redirect the PageLayout add_view to the Page add_view
        """
        return HttpResponseRedirect(reverse('admin:pagemanager_page_add'))

    @staticmethod
    def get_default_response_change_url():
        """
        Returns the URL to redirect on standard save of layout.
        """
        return reverse('admin:index')

    def response_change(self, request, obj):
        """
        There are three buttons that you can press when editing a
        PageLayout object:

        - If you press "Save", this redirects you to the admin index page
          (i.e. the Page listing page)
        - If you press "Save and continue editing", this redirects you to
          the current page.
        - If you press "Save and add another", this redirects you to the
          Page add_view.
        """
        opts = obj._meta
        verbose_name = opts.verbose_name
        if obj._deferred:
            opts_ = opts.proxy_for_model._meta
            verbose_name = opts_.verbose_name

        msg = 'The page "%(obj)s" was changed successfully.' % {
            'name': force_unicode(verbose_name),
            'obj': force_unicode(obj.page.all()[0])
        }
        if "_continue" in request.POST:
            self.message_user(request, msg + ' ' + \
                "You may edit it again below.")
            return HttpResponseRedirect(request.path)
        elif "_addanother" in request.POST:
            self.message_user(request, msg + ' ' + \
                "You may add another page below.")
            return HttpResponseRedirect(
                reverse('admin:pagemanager_page_add')
            )
        else:
            url = self.get_default_response_change_url()
            return HttpResponseRedirect(url)

# Dynamically register admins for each registered PageLayout subclass
for page_layout in pagemanager_site._registry:
    # Overrides provided in the PageLayout subclass' PageManagerMeta class
    meta = page_layout._meta
    pm_meta = page_layout._pagemanager_meta
    # Dynamically create a subclass of PageLayoutAdmin for this
    # particular layout. The reason we do this is that we don't want
    # to pollute PageLayoutAdmin with layout-sepcific settings. Otherwise
    # settings from one layout might bleed through to the next.
    class_body_dict = dict(PageLayoutAdmin.__dict__)
    if pm_meta.admin_mixin_dict:
        class_body_dict.update(pm_meta.admin_mixin_dict)
    # Create a ``Media`` class for ``admin_media_js`` or ``admin_media_css``
    # fields and attach to the Admin class.
    if pm_meta.admin_media_js or pm_meta.admin_media_css:
        css = pm_meta.admin_media_css or {}
        js = pm_meta.admin_media_js or ()
        class_body_dict['media'] = Media(css=css, js=js)
    # Create an Admin class dynamically.
    layoutadmin_cls = type(
        PageLayoutAdmin.__name__,
        (PageLayoutAdmin,),
        class_body_dict
    )
    
    if pm_meta.formfield_overrides:
        layoutadmin_cls.formfield_overrides.update(pm_meta.formfield_overrides)
    if pm_meta.fieldsets:
        layoutadmin_cls.fieldsets = pm_meta.fieldsets
    if meta.verbose_name:
        layoutadmin_cls.verbose_name = meta.verbose_name
    if meta.verbose_name_plural:
        layoutadmin_cls.verbose_name_plural = meta.verbose_name_plural
    if pm_meta.inlines:
        layoutadmin_cls.inlines = PageLayoutAdmin.inlines + pm_meta.inlines
    if pm_meta.readonly_fields:
        layoutadmin_cls.readonly_fields = pm_meta.readonly_fields
    if pm_meta.exclude:
        layoutadmin_cls.exclude = PageLayoutAdmin.exclude + pm_meta.exclude

    admin.site.register(page_layout, layoutadmin_cls)
